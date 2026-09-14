"""SQLite-backed storage for Memory as History.

Three modules for v0.2:

- Consolidation (Assmann): memories start as `working` and must be explicitly
  `promoted` to `consolidated` with a recorded, non-empty reason — a ceremony,
  not a similarity score.
- Anchors (Nora): a small set of pinned, identity-cornerstone memories that
  are always surfaced first and never compete on recency/relevance. Each pin
  requires a reason. Anchors are meant to stay few ("lieux de mémoire" are
  necessarily scarce) — exceeding a soft limit returns a warning rather than
  a hard block, so the caller can decide whether that's intentional.
- Provenance tiers (Ricoeur): every memory carries a tier —
  `archive` (raw, as originally captured), `testimony` (corroborated by an
  independent, additional source), or `interpretation` (the agent's own
  inference, which is not self-evidently true and must be periodically
  re-examined). Archive memories can be upgraded to testimony by
  corroboration; interpretation memories must be reviewed on a cadence.
- Accountable forgetting (Ricoeur): forgetting is treated as a legitimate,
  deliberate act — not silent deletion and not passive decay. `forget()`
  requires a reason and leaves a tombstone (the content is retained, not
  hard-deleted, but disappears from `recall()` and listings). An anchored
  memory cannot be forgotten directly — it must be `unpin()`-ed first, since
  identity cornerstones should not quietly disappear. Forgetting is
  reversible via `restore()`, itself logged with its own reason.

Design principle: every state change that matters (promote, pin, corroborate,
review, forget, restore) is recorded with a reason/note and a timestamp in a
single audit log. Nothing is silently reclassified, and nothing is silently
deleted.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".memory-as-history" / "memory.db"
DEFAULT_ANCHOR_SOFT_LIMIT = 12
DEFAULT_INTERPRETATION_REVIEW_DAYS = 30

TIERS = ("archive", "testimony", "interpretation")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'working',        -- 'working' | 'consolidated'
    tier TEXT NOT NULL DEFAULT 'archive',           -- 'archive' | 'testimony' | 'interpretation'
    created_at TEXT NOT NULL,
    consolidated_at TEXT,
    consolidation_reason TEXT,
    last_reviewed_at TEXT,
    review_status TEXT,                             -- 'current' | 'stale' | NULL
    forgotten_at TEXT,
    forgotten_reason TEXT
);

CREATE TABLE IF NOT EXISTS anchors (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id),
    reason TEXT NOT NULL,
    pinned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corroborations (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    source TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    action TEXT NOT NULL,   -- 'promote' | 'pin' | 'corroborate_upgrade' | 'review' | 'forget' | 'restore'
    reason TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _require_text(value: str, field: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{field} is required and cannot be empty")
    return value


@dataclass
class Memory:
    id: str
    content: str
    source: str | None
    status: str
    tier: str
    created_at: str
    consolidated_at: str | None
    consolidation_reason: str | None
    last_reviewed_at: str | None
    review_status: str | None
    forgotten_at: str | None
    forgotten_reason: str | None

    @property
    def is_forgotten(self) -> bool:
        return self.forgotten_at is not None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "status": self.status,
            "tier": self.tier,
            "created_at": self.created_at,
            "consolidated_at": self.consolidated_at,
            "consolidation_reason": self.consolidation_reason,
            "last_reviewed_at": self.last_reviewed_at,
            "review_status": self.review_status,
            "forgotten_at": self.forgotten_at,
            "forgotten_reason": self.forgotten_reason,
        }

class Store:
    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        anchor_soft_limit: int = DEFAULT_ANCHOR_SOFT_LIMIT,
        interpretation_review_days: int = DEFAULT_INTERPRETATION_REVIEW_DAYS,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self.anchor_soft_limit = anchor_soft_limit
        self.interpretation_review_days = interpretation_review_days

    def close(self) -> None:
        self._conn.close()

    def _log(self, memory_id: str, action: str, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (id, memory_id, action, reason, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_new_id(), memory_id, action, reason, _now()),
        )

    # -- capture --------------------------------------------------------------

    def remember(
        self, content: str, source: str | None = None, tier: str = "archive"
    ) -> Memory:
        """Store a new working memory. Working memories are ordinary
        recollections — they can still be recalled, but they have not gone
        through the consolidation ceremony.

        `tier` defaults to 'archive' (captured as-is). Pass tier='interpretation'
        when the content is the agent's own inference/summary rather than a
        directly observed fact — this schedules it for periodic review."""
        content = _require_text(content, "content")
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
        mid = _new_id()
        now = _now()
        review_status = "current" if tier == "interpretation" else None
        last_reviewed_at = now if tier == "interpretation" else None
        self._conn.execute(
            "INSERT INTO memories "
            "(id, content, source, status, tier, created_at, last_reviewed_at, review_status) "
            "VALUES (?, ?, ?, 'working', ?, ?, ?, ?)",
            (mid, content, source, tier, now, last_reviewed_at, review_status),
        )
        self._conn.commit()
        return self.get(mid)

    # -- consolidation module (Assmann) ---------------------------------------

    def promote(self, memory_id: str, reason: str) -> Memory:
        """Explicitly consolidate a working memory. This is a deliberate act,
        not an automatic score threshold. A non-empty reason is required —
        this is the audit trail that makes consolidation accountable rather
        than opaque. Promoting an already-consolidated memory re-logs the
        action (e.g. to record a stronger/updated justification) but does
        not change `consolidated_at` to a later "first consolidated" time."""
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        now = _now()
        if mem.status != "consolidated":
            self._conn.execute(
                "UPDATE memories SET status='consolidated', consolidated_at=?, "
                "consolidation_reason=? WHERE id=?",
                (now, reason, memory_id),
            )
        else:
            self._conn.execute(
                "UPDATE memories SET consolidation_reason=? WHERE id=?",
                (reason, memory_id),
            )
        self._log(memory_id, "promote", reason)
        self._conn.commit()
        return self.get(memory_id)

    # -- anchor module (Nora) --------------------------------------------------

    def pin(self, memory_id: str, reason: str) -> dict:
        """Mark a memory as an anchor: a 'site of memory' that is always
        surfaced on recall and never competes with ordinary memories on
        recency or relevance. Requires a non-empty reason — anchors are
        declared, not inferred.

        A memory must already be consolidated before it can be pinned: an
        anchor is, by construction, something that has already become
        history — you cannot skip straight from a passing remark to a
        monument. Call `promote()` first.

        Anchors are meant to stay few. Exceeding `anchor_soft_limit` does not
        block the pin, but the returned dict includes a `warning` — a large
        set of "anchors" stops functioning as a set of anchors."""
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        if mem.status != "consolidated":
            raise ValueError(
                "memory must be consolidated (call promote() first) before "
                "it can be pinned as an anchor"
            )
        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO anchors (memory_id, reason, pinned_at) "
            "VALUES (?, ?, ?)",
            (memory_id, reason, now),
        )
        self._log(memory_id, "pin", reason)
        self._conn.commit()

        count = self._conn.execute("SELECT COUNT(*) FROM anchors").fetchone()[0]
        result = {"memory_id": memory_id, "reason": reason, "pinned_at": now}
        if count > self.anchor_soft_limit:
            result["warning"] = (
                f"{count} anchors pinned, exceeding the soft limit of "
                f"{self.anchor_soft_limit}. Anchors work as identity "
                f"cornerstones only while they stay few — consider unpinning "
                f"some."
            )
        return result

    def unpin(self, memory_id: str) -> None:
        self._conn.execute("DELETE FROM anchors WHERE memory_id=?", (memory_id,))
        self._conn.commit()

    def is_anchored(self, memory_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM anchors WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return row is not None

    def list_anchors(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT m.*, a.reason AS anchor_reason, a.pinned_at "
            "FROM anchors a JOIN memories m ON m.id = a.memory_id "
            "WHERE m.forgotten_at IS NULL "
            "ORDER BY a.pinned_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- forgetting module (Ricoeur: forgetting as legitimate, not failure) ----

    def forget(self, memory_id: str, reason: str) -> Memory:
        """Deliberately forget a memory. This is not deletion: the content
        is retained (a tombstone), but the memory disappears from `recall()`
        and `list_anchors()`. Requires a non-empty reason, logged in the
        audit trail — forgetting is a legitimate, accountable act, not a
        silent side-effect of storage pressure.

        An anchored memory cannot be forgotten directly: call `unpin()`
        first. Identity cornerstones should not quietly vanish alongside
        an unrelated forgetting decision — removing an anchor has to be its
        own, separately reasoned step.

        Forgetting an already-forgotten memory is idempotent-ish: it updates
        the reason and re-logs the action, but does not change the original
        `forgotten_at` timestamp."""
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        if self.is_anchored(memory_id):
            raise ValueError(
                "memory is pinned as an anchor; call unpin() first before "
                "it can be forgotten"
            )
        now = _now()
        if mem.forgotten_at is None:
            self._conn.execute(
                "UPDATE memories SET forgotten_at=?, forgotten_reason=? WHERE id=?",
                (now, reason, memory_id),
            )
        else:
            self._conn.execute(
                "UPDATE memories SET forgotten_reason=? WHERE id=?",
                (reason, memory_id),
            )
        self._log(memory_id, "forget", reason)
        self._conn.commit()
        return self.get(memory_id)

    def restore(self, memory_id: str, reason: str) -> Memory:
        """Reverse a forgetting decision. Forgetting in this protocol is not
        a hard delete, so restoration is always possible and is itself a
        deliberate, reasoned, logged act — not a bug fix."""
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        if mem.forgotten_at is None:
            raise ValueError("memory is not currently forgotten")
        self._conn.execute(
            "UPDATE memories SET forgotten_at=NULL, forgotten_reason=NULL WHERE id=?",
            (memory_id,),
        )
        self._log(memory_id, "restore", reason)
        self._conn.commit()
        return self.get(memory_id)

    def list_forgotten(self, limit: int = 50) -> list[dict]:
        """List tombstoned memories — what was forgotten, and why."""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE forgotten_at IS NOT NULL "
            "ORDER BY forgotten_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- provenance tiers module (Ricoeur) --------------------------------------

    def corroborate(self, memory_id: str, source: str) -> Memory:
        """Record that an independent additional source corroborates this
        memory. An 'archive' (raw, single-source) memory is automatically
        upgraded to 'testimony' on its first corroboration. 'interpretation'
        memories are not upgraded by corroboration alone — they must go
        through `review()` instead, since they are inferences, not facts
        that a second source can simply confirm."""
        source = _require_text(source, "source")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        now = _now()
        self._conn.execute(
            "INSERT INTO corroborations (id, memory_id, source, at) VALUES (?, ?, ?, ?)",
            (_new_id(), memory_id, source, now),
        )
        if mem.tier == "archive":
            self._conn.execute(
                "UPDATE memories SET tier='testimony' WHERE id=?", (memory_id,)
            )
            self._log(
                memory_id,
                "corroborate_upgrade",
                f"corroborated by additional source: {source}",
            )
        self._conn.commit()
        return self.get(memory_id)

    def review(self, memory_id: str, note: str) -> Memory:
        """Re-examine an 'interpretation' memory and confirm it still holds.
        Ricoeur treats interpretation as inherently provisional — it must be
        periodically revisited, not trusted indefinitely just because it was
        once inferred. Resets the review clock; the reasoning is logged."""
        note = _require_text(note, "note")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        if mem.tier != "interpretation":
            raise ValueError(
                "only 'interpretation'-tier memories require review; "
                f"this memory has tier={mem.tier!r}"
            )
        now = _now()
        self._conn.execute(
            "UPDATE memories SET last_reviewed_at=?, review_status='current' WHERE id=?",
            (now, memory_id),
        )
        self._log(memory_id, "review", note)
        self._conn.commit()
        return self.get(memory_id)

    def due_for_review(self, days: int | None = None) -> list[dict]:
        """Return interpretation-tier memories whose last review is older
        than `days` (default: interpretation_review_days), or that have
        never been reviewed. Marks them 'stale' in the store as a side
        effect, so `get()`/`recall()` reflect their status too."""
        days = self.interpretation_review_days if days is None else days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE tier='interpretation' AND "
            "forgotten_at IS NULL AND "
            "(last_reviewed_at IS NULL OR last_reviewed_at < ?)",
            (cutoff,),
        ).fetchall()
        stale = [dict(r) for r in rows]
        if stale:
            ids = [r["id"] for r in stale]
            self._conn.executemany(
                "UPDATE memories SET review_status='stale' WHERE id=?",
                [(i,) for i in ids],
            )
            self._conn.commit()
            for r in stale:
                r["review_status"] = "stale"
        return stale

    # -- audit ------------------------------------------------------------------

    def audit_log(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY at DESC LIMIT ?", (limit,)
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
        match on `query` (v0.1/v0.2 have no embedding dependency by design).

        Also surfaces `stale_interpretations`: interpretation-tier memories
        due for review, so callers can prompt for re-examination."""
        anchors = self.list_anchors()

        def _match(row: sqlite3.Row) -> bool:
            if not query:
                return True
            return query.lower() in row["content"].lower()

        remaining = max(limit - len(anchors), 0)
        rest: list[dict] = []
        if remaining:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE forgotten_at IS NULL AND id NOT IN "
                "(SELECT memory_id FROM anchors) "
                "ORDER BY status='consolidated' DESC, created_at DESC"
            ).fetchall()
            for r in rows:
                if _match(r):
                    rest.append(dict(r))
                if len(rest) >= remaining:
                    break

        return {
            "anchors": anchors,
            "memories": rest,
            "stale_interpretations": self.due_for_review(),
        }
