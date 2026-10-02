"""Per-user memory service implementing the AML Add/Search semantics.

Isolation by construction: each ``user_id`` owns one SQLite file, so
cross-user retrieval is impossible — the Search scope and the write target
are the same physical database.

Write path: one transaction per Add request. The idempotency marker
(``aml_add_log``), the memory rows and the per-session position counter are
committed together, so a replay with the same ``request_id`` either finds a
completed marker (and is echoed without re-writing) or re-runs from a clean
rollback. There is no partial-write state to recover.

Read path: reuses the project ``Store.recall`` (BM25, anchors/canon
priority) and optionally ``Store.search`` (hybrid) on the same user
database, flattened to the AML ``data`` array. The memories table is the
project schema; the adapter only adds two ``aml_``-prefixed bookkeeping
tables.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

from ..storage import (
    MIGRATIONS,
    KNOWLEDGE_SCHEMA,
    SCHEMA,
    Store,
    _looks_injected,
    _now,
    _tokenize,
)
from .contract import ContractError, add_ok, parse_add, parse_search
from .chunking import split_long_message
from .governance import govern_entries, record_possible_updates
from .temporal import detect_temporal_clues, parse_iso_ts, time_weight

DEFAULT_DATA_DIR = Path.home() / ".memory-as-history" / "aml"

# Adapter-owned bookkeeping tables. Prefix ``aml_`` keeps them clearly
# separated from the project schema.
AML_SCHEMA = """
CREATE TABLE IF NOT EXISTS aml_add_log (
    request_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aml_sessions (
    session_id    TEXT PRIMARY KEY,
    next_position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS aml_updates (
    memory_id     TEXT NOT NULL,
    superseded_by TEXT NOT NULL,
    similarity    REAL NOT NULL,
    at            TEXT NOT NULL,
    PRIMARY KEY (memory_id, superseded_by)
);
"""

# Column-for-column mirror of Store.remember's INSERT (storage.py). Keeping
# the two write paths aligned is enforced by tests/test_aml_adapter.py.
_MEMORY_COLUMNS = (
    "id, content, source, status, tier, created_at, last_reviewed_at, "
    "review_status, security_sensitive, frame, content_tokens, event_at, "
    "session_id, session_position, material_type, origin_id, capture_context"
)
_MEMORY_PLACEHOLDERS = ", ".join("?" for _ in range(17))


def _migrate(conn: sqlite3.Connection) -> None:
    """Mirror of ``Store._migrate``: apply additive column migrations so the
    adapter-written rows have exactly the same columns as Store-written rows."""
    for table, column, definition in MIGRATIONS:
        cols = {
            r["name"]
            for r in conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )


def _user_db_path(data_dir: Path, user_id: str) -> Path:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
    return data_dir / "users" / f"{digest}.db"


def _open_connection(data_dir: Path, user_id: str) -> sqlite3.Connection:
    db_path = _user_db_path(data_dir, user_id)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    # Project schema + adapter bookkeeping share one creation script, then the
    # same additive migrations Store applies. executescript commits any open
    # transaction first; the script itself carries the BEGIN IMMEDIATE so
    # creation and upgrades share the lock.
    conn.executescript(
        "BEGIN IMMEDIATE;\n" + SCHEMA + KNOWLEDGE_SCHEMA + AML_SCHEMA
    )
    _migrate(conn)
    conn.commit()
    return conn


def _normalize_ts(value: str | None) -> str | None:
    """Normalize project ISO timestamps to the official ``Z`` form."""
    if not value:
        return value
    return value.replace("+00:00", "Z")


def _audit_expand(conn: sqlite3.Connection, row: dict, at: str) -> None:
    """Audit one neighbor-expansion decision (mirrors Store._log)."""
    conn.execute(
        "INSERT INTO audit_log (id, memory_id, action, reason, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex[:12],
            row["id"],
            "aml_turn_expand",
            f"expanded_from_seed position={row.get('session_position')}",
            at,
        ),
    )


def _event_at(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return _dt.datetime.fromtimestamp(
        timestamp / 1000.0, tz=_dt.timezone.utc
    ).isoformat()


class MemoryService:
    """AML-facing memory service for one data directory.

    Thread-safe: every call opens its own SQLite connection (or its own
    ``Store``), and per-user write serialization comes from SQLite's own
    ``BEGIN IMMEDIATE`` + busy timeout.
    """

    def __init__(
        self,
        data_dir: str | Path = DEFAULT_DATA_DIR,
        search_mode: str = "lexical",
        semantic_min: float = 0.85,
        use_temporal: bool = True,
    ):
        self.data_dir = Path(data_dir)
        (self.data_dir / "users").mkdir(parents=True, exist_ok=True)
        if search_mode not in ("lexical", "hybrid", "semantic"):
            raise ValueError(
                f"search_mode must be lexical, hybrid or semantic, got {search_mode!r}"
            )
        if type(semantic_min) is not float or not 0.0 <= semantic_min <= 1.0:
            raise ValueError("semantic_min must be a float in [0, 1]")
        if type(use_temporal) is not bool:
            raise ValueError("use_temporal must be a bool")
        self.search_mode = search_mode
        # Minimum cosine similarity for a semantic-only hit to count as
        # evidence (hybrid/semantic modes). Lexical hits bypass the gate.
        self.semantic_min = semantic_min
        # memory-as-history: rerank evidence by explicit temporal clues
        # ("last year", "上周") so questions about the past retrieve the
        # facts as they stood then. Conservative by design.
        self.use_temporal = use_temporal

    # -- connections -----------------------------------------------------

    def _open_connection(self, user_id: str) -> sqlite3.Connection:
        return _open_connection(self.data_dir, user_id)

    def _user_store(self, user_id: str) -> Store:
        return Store(_user_db_path(self.data_dir, user_id))

    # -- Add -------------------------------------------------------------

    def add(self, payload: object) -> dict:
        req = parse_add(payload)
        conn = self._open_connection(req["user_id"])
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM aml_add_log WHERE request_id = ?",
                (req["request_id"],),
            ).fetchone()
            if row is not None and row["status"] == "done":
                conn.commit()
                return add_ok(
                    req["request_id"], req["user_id"], req["session_id"]
                )
            at = _now()
            conn.execute(
                "INSERT OR REPLACE INTO aml_add_log "
                "(request_id, user_id, session_id, status, at) "
                "VALUES (?, ?, ?, 'done', ?)",
                (req["request_id"], req["user_id"], req["session_id"], at),
            )
            next_pos = self._session_next_position(conn, req["session_id"])
            rows_written = 0
            for i, msg in enumerate(req["messages"]):
                event_at = _event_at(msg["timestamp"])
                # Primary-source chunking: only over-long messages split at
                # sentence boundaries into complete blocks; every block keeps
                # the source event metadata and the message text is preserved
                # as a whole for governance.
                chunks = split_long_message(msg["content"])
                for k, block in enumerate(chunks):
                    mid = self._insert_message(
                        conn,
                        content=block,
                        source=msg["role"],
                        event_at=event_at,
                        session_id=req["session_id"],
                        session_position=next_pos + i + k,
                    )
                    # Write-time governance: record same-fact revisions audited
                    # (superseded_by edges), once per source message using the
                    # FULL original text (never per block). Deterministic,
                    # non-destructive.
                    if k == 0:
                        record_possible_updates(
                            conn,
                            mid,
                            msg["content"],
                            event_at=event_at,
                            session_position=next_pos + i,
                            created_at=at,
                            at=at,
                        )
                rows_written += len(chunks)
            conn.execute(
                "INSERT INTO aml_sessions (session_id, next_position) "
                "VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "next_position = excluded.next_position",
                (req["session_id"], next_pos + rows_written),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return add_ok(req["request_id"], req["user_id"], req["session_id"])

    def _session_next_position(
        self, conn: sqlite3.Connection, session_id: str
    ) -> int:
        row = conn.execute(
            "SELECT next_position FROM aml_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["next_position"]) if row is not None else 0

    def _insert_message(
        self,
        conn: sqlite3.Connection,
        *,
        content: str,
        source: str,
        event_at: str | None,
        session_id: str,
        session_position: int,
    ) -> str:
        """Insert one memory row exactly as Store.remember would (archive
        tier, working status, auto sensitivity screening). Returns the new
        memory id."""
        mid = uuid.uuid4().hex[:12]
        now = _now()
        auto_reason = _looks_injected(content)
        conn.execute(
            f"INSERT INTO memories ({_MEMORY_COLUMNS}) "
            f"VALUES ({_MEMORY_PLACEHOLDERS})",
            (
                mid,
                content,
                source,
                "working",
                "archive",
                now,
                None,
                None,
                int(bool(auto_reason)),
                None,
                json.dumps(_tokenize(content)),
                event_at,
                session_id,
                session_position,
                "unspecified",
                None,
                None,
            ),
        )
        if auto_reason:
            conn.execute(
                "INSERT INTO audit_log (id, memory_id, action, reason, at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:12], mid, "auto_flag_sensitive", auto_reason, now),
            )
        return mid

    # -- Search ----------------------------------------------------------

    def search(self, payload: object) -> dict:
        req = parse_search(payload)
        store = self._user_store(req["user_id"])
        data: list[dict] = []
        try:
            if self.search_mode == "lexical":
                result = store.recall(req["query"], limit=req["top_k"])
            else:
                result = store.search(
                    req["query"], limit=req["top_k"], mode=self.search_mode
                )
            flat: list[dict] = []
            # Score field surfaced to the contract: BM25 relevance for
            # lexical mode, the RRF/hybrid combined score otherwise.
            score_key = "relevance" if self.search_mode == "lexical" else "search_score"
            for section in ("anchors", "canon", "memories"):
                for row in result.get(section, []):
                    # Drop ordinary memories that carry no evidence signal:
                    # they would only pollute the answer generator. Anchors/
                    # canon are identity/consensus entries and always
                    # eligible. In hybrid/semantic modes a row qualifies when
                    # the combined score is positive AND it has either a
                    # lexical hit (BM25 > 0) or a semantic hit above the
                    # similarity threshold — a pure-semantic match whose
                    # similarity is too low is noise, not evidence.
                    if section == "memories":
                        if self.search_mode == "lexical":
                            rel = row.get("relevance")
                            if not isinstance(rel, (int, float)) or rel <= 0:
                                continue
                        else:
                            combined = row.get("search_score")
                            if not isinstance(combined, (int, float)) or combined <= 0:
                                continue
                            rel = row.get("relevance")
                            sem = row.get("semantic_similarity")
                            has_lex = isinstance(rel, (int, float)) and rel > 0
                            has_sem = (
                                isinstance(sem, (int, float))
                                and sem >= self.semantic_min
                            )
                            if not (has_lex or has_sem):
                                continue
                    flat.append(row)
            # Neighbor expansion: nearby turns of ranked seeds carry the
            # extra evidence multi-hop questions need. Only turns sharing a
            # query token are added; the Top K budget still bounds the total.
            seeds = [
                r
                for r in flat
                if r.get("session_id") is not None
                and r.get("session_position") is not None
            ]
            if seeds:
                flat.extend(
                    self._expand_neighbors(
                        store._conn, seeds, req["query"], req["top_k"]
                    )
                )
            data = [self._entry(r, score_key) for r in flat]
            # Temporal clues ("where did she live LAST YEAR?") rerank by the
            # time the facts were true, not by today's ranking. Conservative
            # multipliers; strongest matching clue wins per row.
            clues = detect_temporal_clues(req["query"]) if self.use_temporal else []
            if clues and data:
                # Prefer the message's own timestamp (event_at, spans the real
                # conversation time) over the write timestamp (created_at),
                # which is identical for messages ingested in one /add batch.
                conn = store._conn
                event_map: dict[str, str | None] = {}
                if conn is not None:
                    try:
                        ids = [item["id"] for item in data]
                        q = (
                            "SELECT id, event_at FROM memories "
                            f"WHERE id IN ({','.join('?' * len(ids))})"
                        )
                        for row in conn.execute(q, ids).fetchall():
                            event_map[row["id"]] = row["event_at"]
                    except sqlite3.Error:
                        pass  # fall back to created_at
                stamps = [
                    parse_iso_ts(event_map.get(item["id"]) or item.get("created_at"))
                    for item in data
                ]
                now_ts = max((s for s in stamps if s is not None), default=None)
                if now_ts is not None:
                    for item in data:
                        ts = parse_iso_ts(
                            event_map.get(item["id"]) or item.get("created_at")
                        )
                        if ts is None:
                            continue
                        factor = max(
                            time_weight(ts, now_ts, clue) for clue in clues
                        )
                        if factor != 1.0 and "score" in item:
                            item["score"] = item["score"] * factor
                    if conn is not None:
                        try:
                            for clue in clues:
                                conn.execute(
                                    "INSERT INTO audit_log (id, memory_id, action, reason, at) "
                                    "VALUES (?, NULL, ?, ?, ?)",
                                    (
                                        uuid.uuid4().hex[:12],
                                        "aml_temporal_hit",
                                        f"clue={clue.label} offset={int(clue.offset_seconds)}",
                                        _now(),
                                    ),
                                )
                            conn.commit()
                        except sqlite3.Error:
                            pass  # audit must never break a search
            # Read-time governance: deduplicate identical content and
            # discount older versions of the same fact (audited,
            # non-destructive). Must run before the Store connection closes.
            data = govern_entries(data, _now(), store._conn)
        finally:
            store.close()
        # Global budget: official Top K bounds the returned evidence.
        data = data[: req["top_k"]]
        return {"data": data}

    def _entry(self, row: dict, score_key: str = "relevance") -> dict:
        entry: dict = {"id": row["id"], "content": row["content"]}
        value = row.get(score_key)
        if isinstance(value, (int, float)):
            entry["score"] = float(value)
        created_at = _normalize_ts(row.get("created_at"))
        if created_at:
            entry["created_at"] = created_at
        return entry

    # -- read-time evidence expansion ------------------------------------

    def _expand_neighbors(
        self,
        conn: sqlite3.Connection,
        seed_rows: list[dict],
        query: str,
        top_k: int,
    ) -> list[dict]:
        """Expand each ranked seed to its nearby turns in the same session.

        Multi-evidence questions ("where does X live" after a later "moved
        to Y") often need more than one turn, and the missing turns usually
        sit a few positions away from the seed that did match. This adds
        turns within ``window`` positions of every seed — but only turns
        that share at least one token with the query or with a seed, so
        unrelated filler never enters the evidence window.

        Returns expanded rows ordered by (session_id, session_position),
        deduplicated against the seeds, bounded by the remaining Top K
        budget. Every expansion decision is audited (``aml_turn_expand``).
        """
        budget = max(top_k - len(seed_rows), 0)
        if budget <= 0 or not seed_rows:
            return []
        # Signal set: the query plus everything the ranked seeds talk about.
        # A neighbor that shares no token with the query may still carry the
        # decisive second piece of evidence ("moved to Shanghai" when the
        # query only asks where someone lives), but it must talk about the
        # same subject as a seed — otherwise it is filler, not evidence.
        query_tokens = set(_tokenize(query))
        seed_tokens: set[str] = set()
        for seed in seed_rows:
            seed_tokens |= set(_tokenize(seed["content"]))
        signal = query_tokens | seed_tokens
        if not signal:
            return []
        window = int(os.environ.get("AML_EXPAND_RADIUS", "3"))
        seen_ids = {r["id"] for r in seed_rows}
        found: dict[tuple[str, int], dict] = {}
        now = _now()
        for seed in seed_rows:
            session_id = seed.get("session_id")
            position = seed.get("session_position")
            if session_id is None or position is None:
                continue
            rows = conn.execute(
                "SELECT * FROM memories WHERE forgotten_at IS NULL "
                "AND session_id = ? AND session_position BETWEEN ? AND ?",
                (session_id, position - window, position + window),
            ).fetchall()
            for row in rows:
                if row["id"] in seen_ids:
                    continue
                tokens = set(_tokenize(row["content"]))
                hits = len(signal & tokens)
                if hits <= 0:
                    continue
                key = (session_id, row["session_position"])
                if key in found and found[key]["relevance"] >= hits:
                    continue
                expanded = dict(row)
                # Deliberately small score: expansion rows are supplementary
                # evidence and must rank below every BM25-ranked seed, while
                # still carrying a positive, comparable signal.
                expanded["relevance"] = float(hits) * 0.01
                found[key] = expanded
        ordered = [found[k] for k in sorted(found)]
        for row in ordered[:budget]:
            _audit_expand(conn, row, now)
        return ordered[:budget]
