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
    ):
        self.data_dir = Path(data_dir)
        (self.data_dir / "users").mkdir(parents=True, exist_ok=True)
        if search_mode not in ("lexical", "hybrid", "semantic"):
            raise ValueError(
                f"search_mode must be lexical, hybrid or semantic, got {search_mode!r}"
            )
        self.search_mode = search_mode

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
            for i, msg in enumerate(req["messages"]):
                self._insert_message(
                    conn,
                    content=msg["content"],
                    source=msg["role"],
                    event_at=_event_at(msg["timestamp"]),
                    session_id=req["session_id"],
                    session_position=next_pos + i,
                )
            conn.execute(
                "INSERT INTO aml_sessions (session_id, next_position) "
                "VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "next_position = excluded.next_position",
                (req["session_id"], next_pos + len(req["messages"])),
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
    ) -> None:
        """Insert one memory row exactly as Store.remember would (archive
        tier, working status, auto sensitivity screening)."""
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

    # -- Search ----------------------------------------------------------

    def search(self, payload: object) -> dict:
        req = parse_search(payload)
        store = self._user_store(req["user_id"])
        try:
            if self.search_mode == "lexical":
                result = store.recall(req["query"], limit=req["top_k"])
            else:
                result = store.search(
                    req["query"], limit=req["top_k"], mode=self.search_mode
                )
        finally:
            store.close()
        data: list[dict] = []
        for section in ("anchors", "canon", "memories"):
            for row in result.get(section, []):
                data.append(self._entry(row))
        # Global budget: official Top K bounds the returned evidence.
        data = data[: req["top_k"]]
        return {"data": data}

    def _entry(self, row: dict) -> dict:
        entry: dict = {"id": row["id"], "content": row["content"]}
        relevance = row.get("relevance")
        if isinstance(relevance, (int, float)):
            entry["score"] = float(relevance)
        created_at = _normalize_ts(row.get("created_at"))
        if created_at:
            entry["created_at"] = created_at
        return entry
