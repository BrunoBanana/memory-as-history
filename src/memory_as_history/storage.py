"""SQLite-backed storage for Memory as History.

Two modules for v0.1:
- Consolidation: memories start as `working` and must be explicitly `promoted`
  to `consolidated` with a recorded reason (a ceremony, not a similarity score).
- Anchors: a small set of pinned, identity-cornerstone memories that are always
  surfaced first and never compete on recency/relevance. Each pin requires a
  reason (why this became a "site of memory").

Design principle: every state change that matters (promote, pin) is recorded
with a reason and a timestamp. Nothing is silently reclassified.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".memory-as-history" / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'working',  -- 'working' | 'consolidated'
    created_at TEXT NOT NULL,
    consolidated_at TEXT,
    consolidation_reason TEXT
);

CREATE TABLE IF NOT EXISTS anchors (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id),
    reason TEXT NOT NULL,
    pinned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consolidation_log (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    action TEXT NOT NULL,   -- 'promote' | 'pin'
    reason TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Memory:
    id: str
    content: str
    source: str | None
    status: str
    created_at: str
    consolidated_at: str | None
    consolidation_reason: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "consolidated_at": self.consolidated_at,
            "consolidation_reason": self.consolidation_reason,
        }


class Store:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- consolidation module -------------------------------------------------

    def remember(self, content: str, source: str | None = None) -> Memory:
        """Store a new working memory. Working memories are ordinary,
        unconsolidated recollections — they can still be recalled, but they
        have not gone through the consolidation ceremony."""
        mid = _new_id()
        now = _now()
        self._conn.execute(
            "INSERT INTO memories (id, content, source, status, created_at) "
            "VALUES (?, ?, ?, 'working', ?)",
            (mid, content, source, now),
        )
        self._conn.commit()
        return self.get(mid)

    def promote(self, memory_id: str, reason: str) -> Memory:
        """Explicitly consolidate a working memory. This is a deliberate act,
        not an automatic score threshold. A reason is required — this is the
        audit trail that makes consolidation accountable rather than opaque."""
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        now = _now()
        self._conn.execute(
            "UPDATE memories SET status='consolidated', consolidated_at=?, "
            "consolidation_reason=? WHERE id=?",
            (now, reason, memory_id),
        )
        self._conn.execute(
            "INSERT INTO consolidation_log (id, memory_id, action, reason, at) "
            "VALUES (?, ?, 'promote', ?, ?)",
            (_new_id(), memory_id, reason, now),
        )
        self._conn.commit()
        return self.get(memory_id)

    def consolidation_log(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM consolidation_log ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- anchor module ----------------------------------------------------------

    def pin(self, memory_id: str, reason: str) -> dict:
        """Mark a memory as an anchor: a site of memory that is always
        surfaced and never competes with ordinary memories on recency or
        relevance. Requires a reason — anchors are declared, not inferred."""
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO anchors (memory_id, reason, pinned_at) "
            "VALUES (?, ?, ?)",
            (memory_id, reason, now),
        )
        self._conn.execute(
            "INSERT INTO consolidation_log (id, memory_id, action, reason, at) "
            "VALUES (?, ?, 'pin', ?, ?)",
            (_new_id(), memory_id, reason, now),
        )
        self._conn.commit()
        return {"memory_id": memory_id, "reason": reason, "pinned_at": now}

    def unpin(self, memory_id: str) -> None:
        self._conn.execute("DELETE FROM anchors WHERE memory_id=?", (memory_id,))
        self._conn.commit()

    def list_anchors(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT m.*, a.reason AS anchor_reason, a.pinned_at "
            "FROM anchors a JOIN memories m ON m.id = a.memory_id "
            "ORDER BY a.pinned_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- retrieval ----------------------------------------------------------

    def get(self, memory_id: str) -> Memory | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return Memory(**dict(row))

    def recall(self, query: str | None = None, limit: int = 10) -> dict:
        """Recall memories. Anchors are always returned first, in full,
        regardless of the query — they do not compete on relevance.
        Remaining slots are filled by consolidated memories, then working
        memories, newest first, optionally filtered by a naive substring
        match on `query` (v0.1 has no embedding dependency by design)."""
        anchors = self.list_anchors()

        def _match(row: sqlite3.Row) -> bool:
            if not query:
                return True
            return query.lower() in row["content"].lower()

        remaining = max(limit - len(anchors), 0)
        rest: list[dict] = []
        if remaining:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE id NOT IN "
                "(SELECT memory_id FROM anchors) "
                "ORDER BY status='consolidated' DESC, created_at DESC"
            ).fetchall()
            for r in rows:
                if _match(r):
                    rest.append(dict(r))
                if len(rest) >= remaining:
                    break

        return {"anchors": anchors, "memories": rest}
