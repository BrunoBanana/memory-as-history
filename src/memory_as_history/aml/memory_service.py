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
import re
import sqlite3
import threading
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
from .governance import (
    govern_entries,
    record_possible_contradictions,
    record_possible_updates,
)
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
CREATE TABLE IF NOT EXISTS aml_summaries (
    session_id TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aml_contradictions (
    memory_id     TEXT NOT NULL,
    conflicts_with TEXT NOT NULL,
    signal        TEXT NOT NULL,
    at            TEXT NOT NULL,
    PRIMARY KEY (memory_id, conflicts_with)
);
CREATE TABLE IF NOT EXISTS aml_time_ctx (
    memory_id TEXT PRIMARY KEY,
    year      INTEGER NOT NULL,
    at        TEXT NOT NULL
);
"""

# Rolling-summary budget (F1: long-history global synthesis). One row of
# "key points" per session; oversized histories keep the earliest head plus
# the latest tail so both the beginning and the recent state stay visible.
SUMMARY_LIMIT = 4000
SUMMARY_HEAD_RATIO = 0.4

# Review-style queries ("总结/回顾/Summarize...") trigger a summary entry;
# ordinary questions must never see one. Conservative on purpose.
_SUMMARY_QUERY_PATTERN = re.compile(
    r"总结|回顾|综述|概括|整体|总体|全部|这些年|这段时间|这段|经历|"
    r"故事|一生|人生|过往|经历过什么|都发生了什么|"
    r"summar|overview|overall|the whole|entire|story|backstory|journey|"
    r"what happened|what have i been through|my life|life events|"
    r"tell me about myself|these years|past few years|"
    r"all the (?:things|events|facts|memories)"
)

# -- v1.5 G2: rule memories ----------------------------------------------
# Write-time: statements that state a rule / standing constraint get
# material_type='rule' so read time can surface them for rule questions.
_RULE_WRITE_PATTERN = re.compile(
    r"规则|规矩|规定|约定|必须|禁止|不允许|不能|只能|要求|条例|政策|制度|"
    r"习惯|惯例|原则|"
    r"每次.{0,8}都|从来(?:不|都)|绝对不|一定(?:要|会|得)|总是|"
    r"不准|不许|别忘了|切记|务必要|无论如何|"
    r"\b(?:always|never|must|rules?|requirement|forbidden|prohibited|"
    r"not allowed|make sure|remember to)\b"
)
# Read-time: questions about what is allowed / required / customary.
_RULE_QUERY_PATTERN = re.compile(
    r"规则|规矩|规定|约定|必须|禁止|不允许|能不能|是否.{0,6}(?:可以|允许|必须)|"
    r"可以.{0,6}吗|怎么办|如何处理|要求|政策|制度|惯例|原则|允许|习惯|"
    r"注意|讲究|忌讳|小心|什么规矩|"
    r"\b(?:rules?|allowed|permitted|must|always|never|policy|regulation|"
    r"habit|custom|requirement|can i|am i allowed)\b"
)


# English rule patterns match case-insensitively (a separate lower-cased
# scan keeps the Chinese patterns byte-exact).
_RULE_WRITE_EN = re.compile(
    r"\b(always|never|must|rules?|requirement|requirements|forbidden|"
    r"prohibited|not allowed|make sure|remember to)\b|"
    r"\bno (?:smoking|eating|drinking|parking|phones?|guests|pets)\b"
)
_RULE_QUERY_EN = re.compile(
    r"\b(rules?|allowed|permitted|must|always|never|policy|regulation|"
    r"habit|custom|requirement|requirements|can i|am i allowed)\b"
)


def _is_rule_statement(text: str) -> bool:
    if not text:
        return False
    if _RULE_WRITE_PATTERN.search(text) is not None:
        return True
    return _RULE_WRITE_EN.search(text.lower()) is not None


def _is_rule_question(query: str) -> bool:
    if not query:
        return False
    if _RULE_QUERY_PATTERN.search(query) is not None:
        return True
    return _RULE_QUERY_EN.search(query.lower()) is not None


# -- v1.6 C1: content years ----------------------------------------------
# Years named inside memory content ("2021年我住在福州", "in 2020 we moved",
# "during 2019"). Stored in aml_time_ctx at write time; a question anchored
# to a year boosts the memories that describe that year.
_CONTENT_YEAR_PATTERN = re.compile(
    r"(20\d\d)年|in (?:the year )?(20\d\d)|during (?:the year )?(20\d\d)"
)


def _content_years(text: str) -> list[int]:
    """Years explicitly named in a memory's content (valid range only)."""
    if not text:
        return []
    out: list[int] = []
    for m in _CONTENT_YEAR_PATTERN.finditer(text):
        for g in m.groups():
            if g is None:
                continue
            y = int(g)
            if 1900 <= y <= 2100:
                out.append(y)
    return out


def _query_years(clues: list) -> set[int]:
    """Years the query anchors to, from detected temporal clues. A year
    question ("in 2021", "2021年") must match the memories that describe
    that year, not the ones written around that date."""
    years: set[int] = set()
    for c in clues:
        if c.anchor_epoch is not None and hasattr(c, "label"):
            if c.label in ("year_in", "year_since", "year_after",
                           "month_year", "month_day", "month_in"):
                years.add(int(_dt.datetime.fromtimestamp(
                    c.anchor_epoch, tz=_dt.timezone.utc).year))
            elif c.label == "year_range":
                # Range clues carry a center-year anchor; re-extract both
                # endpoints from the raw text for an accurate span.
                for y in re.findall(r"(20\d\d)", c.raw):
                    years.add(int(y))
    return {y for y in years if 1900 <= y <= 2100}

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


def _point(text: str, limit: int = 96) -> str:
    """One summary point from a message: first non-empty line, truncated."""
    t = (text or "").strip()
    if not t:
        return ""
    head = t.split("\n")[0].strip()
    return head if len(head) <= limit else head[:limit].rstrip() + "…"


def _update_summary(old: str | None, point: str) -> str:
    """Rolling per-session summary: append the new point (deduped), then
    keep the earliest head plus the latest tail when over budget so both
    the beginning of the history and the recent state stay visible."""
    if not point:
        return old or ""
    parts = old.split("\n") if old else []
    if point in parts:
        return old or ""
    parts.append(point)
    text = "\n".join(parts)
    if len(text) <= SUMMARY_LIMIT:
        return text
    keep_head = int(SUMMARY_LIMIT * SUMMARY_HEAD_RATIO)
    keep_tail = SUMMARY_LIMIT - keep_head - 3  # "\n…\n" separator
    return text[:keep_head] + "\n…\n" + text[-keep_tail:]


def _upsert_summary(conn: sqlite3.Connection, session_id: str, point: str) -> None:
    """Append one point to the session summary inside the Add transaction."""
    row = conn.execute(
        "SELECT summary FROM aml_summaries WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    old = row["summary"] if row is not None else None
    new = _update_summary(old, point)
    if new == old and row is not None:
        return
    conn.execute(
        "INSERT OR REPLACE INTO aml_summaries (session_id, summary, updated_at) "
        "VALUES (?, ?, ?)",
        (session_id, new, _now()),
    )


def _summary_clue(query: str) -> bool:
    """True when the query asks for a global synthesis / review. Ordinary
    factual questions must not be affected."""
    if not query:
        return False
    return _SUMMARY_QUERY_PATTERN.search(query) is not None


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
        max_concurrent_search: int = 2,
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
        # Concurrency gate for the expensive retrieval stage (embedding
        # inference). The AML service runs on a 2 GB host; without this,
        # 16 parallel Search calls can push the E5 model past physical
        # memory and trip the OOM killer (observed 2026-10-04). Requests
        # queue inside the gate instead of exhausting memory. Retrieval
        # logic is unchanged.
        if type(max_concurrent_search) is not int or max_concurrent_search < 1:
            raise ValueError("max_concurrent_search must be an int >= 1")
        self._search_gate = threading.BoundedSemaphore(max_concurrent_search)

    # -- connections -----------------------------------------------------

    def _open_connection(self, user_id: str) -> sqlite3.Connection:
        return _open_connection(self.data_dir, user_id)

    def _user_store(self, user_id: str) -> Store:
        return Store(_user_db_path(self.data_dir, user_id))

    # -- Add -------------------------------------------------------------

    def add(self, payload: object) -> dict:
        req = parse_add(payload)
        print(
            f"[aml-add] rid={req['request_id'][:24]} "
            f"uid={req['user_id'][:24]} sid={req['session_id'][:24]} "
            f"msgs={len(req['messages'])} at={_now()}",
            flush=True,
        )
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
            # Monotonic per-session position cursor: every chunk (a long
            # message may split into several sentence-boundary blocks) gets
            # its own position, so positions are never shared between blocks
            # of different messages (i+k arithmetic collides whenever chunk
            # counts differ).
            cursor = next_pos
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
                        session_position=cursor,
                        # G2: rule statements are marked so read time can
                        # surface them for rule questions.
                        material_type="rule" if _is_rule_statement(msg["content"]) else "unspecified",
                    )
                    cursor += 1
                    # F1: keep a rolling per-session summary so review-style
                    # questions can draw on the whole history, not just the
                    # retrieved window. One point per source message.
                    if k == 0:
                        _upsert_summary(conn, req["session_id"], _point(msg["content"]))
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
                            session_position=cursor - 1,
                            created_at=at,
                            at=at,
                        )
                    # D2: record contradictory statements (same subject,
                    # same fact domain, flipped polarity) so read time can
                    # surface the partner when either side is retrieved.
                    if k == 0:
                        record_possible_contradictions(
                            conn,
                            mid,
                            msg["content"],
                            event_at=event_at,
                            session_position=cursor - 1,
                            created_at=at,
                            at=at,
                        )
                    # C1: extract years named in the content ("2021年我住在
                    # 福州" -> year 2021). Questions anchored to a year then
                    # boost the memories that actually describe that year,
                    # instead of reranking write times — the historical
                    # archive answers "where did she live in 2021?" with the
                    # fact as it stood then.
                    for year in _content_years(block):
                        conn.execute(
                            "INSERT OR IGNORE INTO aml_time_ctx "
                            "(memory_id, year, at) VALUES (?, ?, ?)",
                            (mid, year, at),
                        )
            conn.execute(
                "INSERT INTO aml_sessions (session_id, next_position) "
                "VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "next_position = excluded.next_position",
                (req["session_id"], cursor),
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
        material_type: str = "unspecified",
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
                material_type,
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
        print(
            f"[aml-search] uid={req['user_id'][:24]} "
            f"q={req['query'][:200]} top_k={req.get('top_k')} at={_now()}",
            flush=True,
        )
        store = self._user_store(req["user_id"])
        data: list[dict] = []
        try:
            # The expensive stage (embedding inference) runs inside the
            # concurrency gate. Queued requests wait here; memory stays
            # bounded on the 2 GB host instead of OOM-killing the worker.
            with self._search_gate:
                if self.search_mode == "lexical":
                    result = store.recall(req["query"], limit=req["top_k"])
                else:
                    # candidate_cap: BM25 pre-filters before semantic rerank.
                    # Official Full stores run into thousands of entries;
                    # embedding all of them blows the 2 GB host's request
                    # budget (SEARCH_TIMEOUT, observed 2026-10-05). 300 keeps
                    # top_k=100 well covered while cutting inference ~5-10x.
                    result = store.search(
                        req["query"],
                        limit=req["top_k"],
                        mode=self.search_mode,
                        candidate_cap=300,
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
                # C1 time-context boost: a question anchored to a year
                # ("in 2021", "2021年") boosts the memories whose content
                # names that year, so the archive answers with the fact as
                # it stood then. Runs before write-time rerank so the boost
                # and the temporal window combine, not cancel.
                years = _query_years(clues)
                if years:
                    data = self._time_context_boost(store, data, years)
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
            # Read-time governance: deduplicate identical content, discount
            # older versions of the same fact, and (for current-state
            # questions — no temporal clue) push superseded older versions
            # well down so the answer generator sees the current truth.
            # History-anchored queries keep them for evidence. Audited,
            # non-destructive. Must run before the Store connection closes.
            data = govern_entries(
                data,
                _now(),
                store._conn,
                supersede_discount=None if clues else 0.1,
            )
            # F1: review-style questions ("总结/Summarize...") get global
            # synthesis entries in front of the ranked evidence, so the
            # answer generator can draw on the whole history. Ordinary
            # queries are untouched (no trigger -> no entries).
            if _summary_clue(req["query"]):
                summaries = self._summary_entries(store, req["top_k"])
                if summaries:
                    data = summaries + data
            # G2: rule questions ("可以…吗/有什么规矩/允许吗") surface the
            # standing rule statements ahead of the ranked evidence. Same
            # trigger discipline as summaries: no rule clue -> no entries.
            if _is_rule_question(req["query"]):
                rules = self._rule_entries(store, req["top_k"])
                if rules:
                    data = rules + data
            # D2: when either side of a recorded contradiction is in the
            # evidence, pull in the other side so the answer generator sees
            # the conflict in full instead of one-sided evidence. Appended
            # after the ranked evidence (never ahead of it); Top K bounds
            # the total.
            partners = self._contradiction_partners(store, data)
            if partners:
                data = data + partners
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

    def _get_summaries(self, store: Store) -> list[dict]:
        """All session summaries for this user, building them lazily from the
        memories table when the table is empty (e.g. data written before the
        summary feature shipped). Returns list of (session_id, summary)."""
        conn = store._conn
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT session_id, summary FROM aml_summaries"
        ).fetchall()
        if rows:
            return [(r["session_id"], r["summary"]) for r in rows]
        # Lazy build: group memories by session, one point per content row.
        mems = conn.execute(
            "SELECT session_id, content FROM memories "
            "WHERE forgotten_at IS NULL "
            "ORDER BY session_id, session_position"
        ).fetchall()
        by_session: dict[str, list[str]] = {}
        for r in mems:
            by_session.setdefault(r["session_id"], []).append(r["content"])
        built: list[tuple[str, str]] = []
        now = _now()
        for sid, contents in by_session.items():
            summary = ""
            for content in contents:
                summary = _update_summary(summary, _point(content))
            conn.execute(
                "INSERT OR REPLACE INTO aml_summaries (session_id, summary, updated_at) "
                "VALUES (?, ?, ?)",
                (sid, summary, now),
            )
            built.append((sid, summary))
        if built:
            conn.commit()
        return built

    def _summary_entries(self, store: Store, top_k: int) -> list[dict]:
        """Global-synthesis entries for review-style queries: one entry per
        session summary chunk, placed ahead of the ranked evidence with a
        high score. Empty when nothing is available."""
        summaries = self._get_summaries(store)
        if not summaries:
            return []
        entries: list[dict] = []
        for sid, summary in summaries:
            if not summary:
                continue
            # One entry per session, chunked to a sane size.
            chunk = summary[:2000]
            entries.append({
                "id": f"summary_{hashlib.sha256(sid.encode('utf-8')).hexdigest()[:10]}",
                "content": chunk,
                "score": 999.0,
            })
            if len(entries) >= 3 or len(entries) * 2000 >= top_k:
                break
        return entries

    def _rule_entries(self, store: Store, top_k: int) -> list[dict]:
        """Rule statements (material_type='rule', G2) surfaced ahead of the
        ranked evidence when the query asks about rules / permissions /
        customs. A question like "有什么规矩" shares no token with the
        statements themselves, so BM25 alone would miss them; surfacing makes
        the answer generator see the standing constraints. Newest first:
        a later rule supersedes an earlier one for current-state questions.
        Bounded by Top K and a sanity cap (a dozen rules is already more
        than any single answer needs)."""
        conn = store._conn
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT id, content FROM memories "
            "WHERE forgotten_at IS NULL AND material_type = 'rule' "
            "ORDER BY created_at DESC, session_position DESC LIMIT ?",
            (max(top_k, 12),),
        ).fetchall()
        if not rows:
            return []
        entries = [
            {"id": r["id"], "content": r["content"], "score": 999.0}
            for r in rows
        ]
        return entries[: min(top_k, 12)]

    def _contradiction_partners(
        self, store: Store, entries: list[dict]
    ) -> list[dict]:
        """For every ranked entry that participates in a recorded
        contradiction (D2), fetch the other side's memory row and append it
        as evidence. The partner carries a small positive score so it never
        outranks the ranked evidence, and the pair is deduplicated against
        entries already in the window."""
        if not entries:
            return []
        conn = store._conn
        if conn is None:
            return []
        ids = [e["id"] for e in entries]
        seen = set(ids)
        try:
            q = (
                "SELECT memory_id, conflicts_with, signal FROM aml_contradictions "
                f"WHERE memory_id IN ({','.join('?' * len(ids))}) "
                f"OR conflicts_with IN ({','.join('?' * len(ids))})"
            )
            pairs = conn.execute(q, ids + ids).fetchall()
        except sqlite3.Error:
            return []  # governance must never break a search
        partners: dict[str, tuple[str, str]] = {}
        for row in pairs:
            mine = row["memory_id"] if row["memory_id"] in seen else row["conflicts_with"]
            other = row["conflicts_with"] if row["memory_id"] in seen else row["memory_id"]
            if other in seen:
                continue  # both sides already in the window
            if other not in partners:
                partners[other] = (mine, row["signal"])
        if not partners:
            return []
        ph = ",".join("?" * len(partners))
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories "
            f"WHERE id IN ({ph}) AND forgotten_at IS NULL",
            list(partners),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            mine, signal = partners[r["id"]]
            out.append({
                "id": r["id"],
                "content": r["content"],
                "score": 0.5,  # evidence-grade, below any ranked seed
                "created_at": _normalize_ts(r["created_at"]),
            })
            if conn is not None:
                try:
                    conn.execute(
                        "INSERT INTO audit_log (id, memory_id, action, reason, at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            uuid.uuid4().hex[:12],
                            r["id"],
                            "aml_contradiction_partner",
                            f"partner_of={mine} signal={signal}",
                            _now(),
                        ),
                    )
                except sqlite3.Error:
                    pass
        if conn is not None:
            try:
                conn.commit()
            except sqlite3.Error:
                pass
        return out

    def _time_context_boost(
        self, store: Store, entries: list[dict], years: set[int]
    ) -> list[dict]:
        """Boost memories whose content names one of the query's years.

        Returns a re-ranked copy (scored entries descend by boosted score,
        unscored anchors/canon keep priority). Every boost is audited.
        A no-match run returns the entries untouched.
        """
        if not entries:
            return entries
        conn = store._conn
        if conn is None:
            return entries
        ids = [e["id"] for e in entries]
        try:
            rows = conn.execute(
                "SELECT memory_id FROM aml_time_ctx "
                f"WHERE memory_id IN ({','.join('?' * len(ids))}) "
                f"AND year IN ({','.join('?' * len(years))})",
                ids + sorted(years),
            ).fetchall()
        except sqlite3.Error:
            return entries  # time context must never break a search
        matched = {r["memory_id"] for r in rows}
        if not matched:
            return entries
        out: list[dict] = []
        now = _now()
        for item in entries:
            item = dict(item)
            if item["id"] in matched and "score" in item:
                item["score"] = round(float(item["score"]) * 1.5, 4)
                try:
                    conn.execute(
                        "INSERT INTO audit_log (id, memory_id, action, reason, at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            uuid.uuid4().hex[:12],
                            item["id"],
                            "aml_time_ctx_boost",
                            f"years={','.join(map(str, sorted(years)))}",
                            now,
                        ),
                    )
                except sqlite3.Error:
                    pass
            out.append(item)
        out.sort(key=lambda e: (0 if "score" not in e else 1,
                                -(e.get("score") or 0.0)))
        try:
            conn.commit()
        except sqlite3.Error:
            pass
        return out

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
