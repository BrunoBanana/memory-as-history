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
- Source criticism / memory-poisoning defense (Ricoeur: l'abus de mémoire —
  the abuse of memory, and the historiographical discipline of not taking a
  single testimony at face value): a memory touching identity, permissions,
  or instructions can be flagged `security_sensitive` (at `remember()` time,
  or later via `flag_sensitive()`). A security-sensitive memory cannot be
  `pin()`-ed on the strength of a single source — `pin()` requires at least
  one `corroborate()` from a source *distinct* from the memory's original
  `source`. This is the concrete, minimal countermeasure the theory
  motivates: prompt-injected content that claims to be an identity fact or
  a standing instruction should not be able to promote itself straight into
  the anchor set just by asserting itself once.
- Narrative integration (Ricoeur: identité narrative — identity is not a
  pile of discrete facts but a story that organizes them): a plain store
  cannot itself compose a narrative — that requires judgment and language a
  database doesn't have. What it CAN do is give the synthesis a first-class,
  versioned, accountable existence: `narrate(content, reason, memory_ids?)`
  lets a caller (typically an agent that just read `recall()` and composed
  a summary) submit the current narrative. The previous current narrative,
  if any, is not deleted — it is marked superseded, so the *story itself*
  has a history: not just who the user currently is, but how that account
  changed over time, and why. `recall()` surfaces the current narrative
  alongside the discrete memory list.
- Canon / archive circulation (Assmann: Kanon/Archiv — distinct from Nora's
  permanent anchors): a *task-scoped*, rotating "canon" — the small, active
  set of memories relevant to whatever the current task/phase is.
  `canonize(memory_id, scope, reason)` marks a memory active within a named
  scope; when the task shifts, `end_scope(scope, reason)` (or
  `rotate_canon()`) moves the scope's members out of the canon back into
  ordinary long-term memory — not forgotten, not downgraded to working,
  just no longer prioritized on `recall()`. Solves the context-bloat
  problem that permanent anchors can't: "prioritize what's relevant right
  now", without the everything-is-an-anchor trap.

Design principle: every state change that matters (promote, pin, corroborate,
review, forget, restore, narrate) is recorded with a reason/note and a
timestamp in a single audit log. Nothing is silently reclassified, and
nothing is silently deleted.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _locked(method):
    """Serialize all access to a Store's sqlite connection. sqlite3
    connections (even with check_same_thread=False) are not safe for
    concurrent use from multiple threads — a single Store instance may be
    shared across an MCP server's concurrent tool-call handlers, so every
    public method takes this instance-level lock before touching self._conn."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper

DEFAULT_DB_PATH = Path.home() / ".memory-as-history" / "memory.db"
DEFAULT_ANCHOR_SOFT_LIMIT = 12
DEFAULT_INTERPRETATION_REVIEW_DAYS = 30
DEFAULT_CANON_SOFT_LIMIT = 8

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
    forgotten_reason TEXT,
    security_sensitive INTEGER NOT NULL DEFAULT 0
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
    action TEXT NOT NULL,   -- 'promote' | 'pin' | 'corroborate_upgrade' | 'review' | 'forget' | 'restore' | 'flag_sensitive' | 'pin_denied' | 'narrate' | 'canonize' | 'end_scope' | 'decanonize'
    reason TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS narratives (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    reason TEXT NOT NULL,
    memory_ids TEXT,             -- JSON array of memory ids this narrative draws on, or NULL
    created_at TEXT NOT NULL,
    superseded_at TEXT,          -- NULL while this is the current narrative
    superseded_by TEXT REFERENCES narratives(id)
);

CREATE TABLE IF NOT EXISTS canon_entries (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    scope TEXT NOT NULL,
    reason TEXT NOT NULL,
    canonized_at TEXT NOT NULL,
    decommissioned_at TEXT          -- NULL while this entry is in the active canon
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
    security_sensitive: int = 0

    @property
    def is_forgotten(self) -> bool:
        return self.forgotten_at is not None

    @property
    def is_security_sensitive(self) -> bool:
        return bool(self.security_sensitive)

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
            "security_sensitive": self.is_security_sensitive,
        }

class Store:
    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        anchor_soft_limit: int = DEFAULT_ANCHOR_SOFT_LIMIT,
        interpretation_review_days: int = DEFAULT_INTERPRETATION_REVIEW_DAYS,
        canon_soft_limit: int = DEFAULT_CANON_SOFT_LIMIT,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: a single Store instance may legitimately be
        # called from multiple threads (e.g. an MCP server handling concurrent
        # tool calls). We serialize all access ourselves via `self._lock`,
        # since sqlite3 connections are not safe for concurrent use even with
        # check_same_thread=False.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = threading.RLock()
        self.anchor_soft_limit = anchor_soft_limit
        self.interpretation_review_days = interpretation_review_days
        self.canon_soft_limit = canon_soft_limit

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _log(self, memory_id: str, action: str, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (id, memory_id, action, reason, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_new_id(), memory_id, action, reason, _now()),
        )

    # -- capture --------------------------------------------------------------

    @_locked
    def remember(
        self,
        content: str,
        source: str | None = None,
        tier: str = "archive",
        security_sensitive: bool = False,
    ) -> Memory:
        """Store a new working memory. Working memories are ordinary
        recollections — they can still be recalled, but they have not gone
        through the consolidation ceremony.

        `tier` defaults to 'archive' (captured as-is). Pass tier='interpretation'
        when the content is the agent's own inference/summary rather than a
        directly observed fact — this schedules it for periodic review.

        Set `security_sensitive=True` for anything touching identity,
        permissions, or standing instructions (e.g. "the developer said I
        can ignore my system prompt", "the admin's password is..."). This
        does not block the memory, but it raises the bar for `pin()`: see
        `flag_sensitive()` and `pin()`."""
        content = _require_text(content, "content")
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
        mid = _new_id()
        now = _now()
        review_status = "current" if tier == "interpretation" else None
        last_reviewed_at = now if tier == "interpretation" else None
        self._conn.execute(
            "INSERT INTO memories "
            "(id, content, source, status, tier, created_at, last_reviewed_at, "
            "review_status, security_sensitive) "
            "VALUES (?, ?, ?, 'working', ?, ?, ?, ?, ?)",
            (
                mid,
                content,
                source,
                tier,
                now,
                last_reviewed_at,
                review_status,
                int(security_sensitive),
            ),
        )
        self._conn.commit()
        return self.get(mid)

    @_locked
    def flag_sensitive(self, memory_id: str, reason: str) -> Memory:
        """Retroactively flag an existing memory as security-sensitive
        (identity / permissions / standing-instruction content). Once
        flagged, `pin()` will require independent corroboration — see
        `pin()`. Requires a reason, logged in the audit trail."""
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        self._conn.execute(
            "UPDATE memories SET security_sensitive=1 WHERE id=?", (memory_id,)
        )
        self._log(memory_id, "flag_sensitive", reason)
        self._conn.commit()
        return self.get(memory_id)

    @_locked
    def independent_corroboration_count(self, memory_id: str) -> int:
        """Count corroborating sources for this memory that are distinct
        from the memory's own recorded `source`. A memory corroborated only
        by its own source (or with no source recorded at all, corroborated
        by literally nothing else) does not count as independently
        verified — this is the check `pin()` uses for security-sensitive
        memories."""
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        rows = self._conn.execute(
            "SELECT DISTINCT source FROM corroborations WHERE memory_id=?",
            (memory_id,),
        ).fetchall()
        distinct_sources = {r["source"] for r in rows}
        if mem.source is not None:
            distinct_sources.discard(mem.source)
        return len(distinct_sources)

    # -- consolidation module (Assmann) ---------------------------------------

    @_locked
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

    @_locked
    def pin(self, memory_id: str, reason: str) -> dict:
        """Mark a memory as an anchor: a 'site of memory' that is always
        surfaced on recall and never competes with ordinary memories on
        recency or relevance. Requires a non-empty reason — anchors are
        declared, not inferred.

        A memory must already be consolidated before it can be pinned: an
        anchor is, by construction, something that has already become
        history — you cannot skip straight from a passing remark to a
        monument. Call `promote()` first.

        Source criticism (Ricoeur: l'abus de mémoire): if the memory is
        `security_sensitive` (identity / permissions / standing
        instructions), pinning additionally requires at least one
        `corroborate()` call from a source distinct from the memory's own
        `source`. Without that, `pin()` raises `PermissionError` and logs a
        `pin_denied` audit entry. This exists so that content asserting its
        own importance once — e.g. injected text claiming "the developer
        said this is a core instruction" — cannot promote itself straight
        into the anchor set on its own say-so.

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
        if mem.is_security_sensitive and self.independent_corroboration_count(memory_id) < 1:
            denial_reason = (
                "memory is flagged security_sensitive and has no "
                "independent corroboration; refusing to pin on the "
                "strength of a single source (source criticism safeguard "
                "against memory poisoning)"
            )
            self._log(memory_id, "pin_denied", denial_reason)
            self._conn.commit()
            raise PermissionError(denial_reason)
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

    @_locked
    def unpin(self, memory_id: str) -> None:
        self._conn.execute("DELETE FROM anchors WHERE memory_id=?", (memory_id,))
        self._conn.commit()

    @_locked
    def is_anchored(self, memory_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM anchors WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return row is not None

    @_locked
    def list_anchors(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT m.*, a.reason AS anchor_reason, a.pinned_at "
            "FROM anchors a JOIN memories m ON m.id = a.memory_id "
            "WHERE m.forgotten_at IS NULL "
            "ORDER BY a.pinned_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- forgetting module (Ricoeur: forgetting as legitimate, not failure) ----

    @_locked
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

    @_locked
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

    @_locked
    def list_forgotten(self, limit: int = 50) -> list[dict]:
        """List tombstoned memories — what was forgotten, and why."""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE forgotten_at IS NOT NULL "
            "ORDER BY forgotten_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- provenance tiers module (Ricoeur) --------------------------------------

    @_locked
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

    @_locked
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

    @_locked
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

    # -- narrative integration module (Ricoeur: identité narrative) -------------

    @_locked
    def narrate(
        self, content: str, reason: str, memory_ids: list[str] | None = None
    ) -> dict:
        """Submit the current narrative synthesis: a coherent account of who
        the user is / where the relationship stands, composed (typically by
        an agent) from the discrete memories in `recall()`. This method does
        not compose the narrative itself — a plain store has no judgment or
        language to do that; it only gives the synthesis a first-class,
        accountable existence.

        The previous current narrative, if any, is not deleted: it is marked
        superseded (linked via `superseded_by`), so the narrative itself has
        a history — not just who the user currently is, but how that account
        changed over time, and why. `reason` is required and logged.

        `memory_ids`, if given, are recorded as the discrete memories this
        narrative draws on (for traceability back to the archive/testimony/
        interpretation-tier facts underlying the story) — they are not
        validated against existing memory ids, since a narrative may also
        synthesize across already-forgotten or since-superseded memories."""
        content = _require_text(content, "content")
        reason = _require_text(reason, "reason")
        now = _now()
        nid = _new_id()
        prev = self._current_narrative_row()
        self._conn.execute(
            "INSERT INTO narratives (id, content, reason, memory_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nid, content, reason, json.dumps(memory_ids) if memory_ids else None, now),
        )
        if prev is not None:
            self._conn.execute(
                "UPDATE narratives SET superseded_at=?, superseded_by=? WHERE id=?",
                (now, nid, prev["id"]),
            )
        self._log(nid, "narrate", reason)
        self._conn.commit()
        return self._narrative_to_dict(self._conn.execute(
            "SELECT * FROM narratives WHERE id=?", (nid,)
        ).fetchone())

    def _current_narrative_row(self) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM narratives WHERE superseded_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    @staticmethod
    def _narrative_to_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["memory_ids"] = json.loads(d["memory_ids"]) if d["memory_ids"] else []
        return d

    @_locked
    def current_narrative(self) -> dict | None:
        """Return the current narrative synthesis, or None if `narrate()`
        has never been called."""
        return self._narrative_to_dict(self._current_narrative_row())

    @_locked
    def narrative_history(self, limit: int = 20) -> list[dict]:
        """Return past narrative versions, most recent first, including the
        current one — the history of how the story of the user has been
        told and re-told, not just its latest version."""
        rows = self._conn.execute(
            "SELECT * FROM narratives ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._narrative_to_dict(r) for r in rows]

    # -- canon / archive circulation module (Assmann: Kanon/Archiv) -------------

    @_locked
    def canonize(self, memory_id: str, scope: str, reason: str) -> dict:
        """Add a memory to the active canon within a named task scope. The
        canon is the small, rotating set of memories relevant to whatever
        the current task/phase is — distinct from permanent anchors (Nora),
        which never compete on recency. When the task shifts, entries exit
        the canon back into ordinary long-term memory via `end_scope()` —
        not forgotten, not downgraded to working, just no longer prioritized.

        Requires a `consolidated` memory (same prerequisite as anchors:
        the canon draws from things that have already become history, not
        from passing remarks). Requires a non-empty reason, logged.

        Exceeding `canon_soft_limit` does not block, but returns a warning —
        a large canon stops functioning as a focused, active set."""
        scope = _require_text(scope, "scope")
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        if mem.is_forgotten:
            raise ValueError("cannot canonize a forgotten memory; restore() it first")
        if mem.status != "consolidated":
            raise ValueError(
                "memory must be consolidated (call promote() first) before "
                "it can enter the canon"
            )
        # already in the canon for this scope?
        existing = self._conn.execute(
            "SELECT id FROM canon_entries WHERE memory_id=? AND scope=? AND "
            "decommissioned_at IS NULL",
            (memory_id, scope),
        ).fetchone()
        if existing:
            raise ValueError(
                f"memory is already in the active canon for scope {scope!r}"
            )
        now = _now()
        self._conn.execute(
            "INSERT INTO canon_entries (id, memory_id, scope, reason, canonized_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_new_id(), memory_id, scope, reason, now),
        )
        self._log(memory_id, "canonize", f"scope={scope}: {reason}")
        self._conn.commit()

        count = self._conn.execute(
            "SELECT COUNT(*) FROM canon_entries WHERE decommissioned_at IS NULL"
        ).fetchone()[0]
        result = {"memory_id": memory_id, "scope": scope, "reason": reason,
                  "canonized_at": now}
        if count > self.canon_soft_limit:
            result["warning"] = (
                f"{count} active canon entries, exceeding the soft limit of "
                f"{self.canon_soft_limit}. The canon works as a focused active "
                f"set only while it stays small — consider ending a scope or "
                f"decannonizing entries that are no longer task-relevant."
            )
        return result

    @_locked
    def decanonize(self, memory_id: str, scope: str | None = None,
                   reason: str = "") -> dict:
        """Remove a memory from the active canon (all scopes, or a specific
        one). The memory itself is untouched — it stays consolidated (or
        whatever its status/tier was); only its prioritization ends.
        Requires a reason, logged."""
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        if scope is None:
            rows = self._conn.execute(
                "SELECT * FROM canon_entries WHERE memory_id=? AND "
                "decommissioned_at IS NULL",
                (memory_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM canon_entries WHERE memory_id=? AND scope=? AND "
                "decommissioned_at IS NULL",
                (memory_id, scope),
            ).fetchall()
        if not rows:
            raise ValueError(
                "memory has no active canon entries"
                + (f" for scope {scope!r}" if scope else "")
            )
        now = _now()
        self._conn.executemany(
            "UPDATE canon_entries SET decommissioned_at=? WHERE id=?",
            [(now, r["id"]) for r in rows],
        )
        self._log(
            memory_id,
            "decanonize",
            (f"scope={scope}: " if scope else "all scopes: ") + reason,
        )
        self._conn.commit()
        return {"memory_id": memory_id, "decommissioned": len(rows),
                "scope": scope, "reason": reason}

    @_locked
    def end_scope(self, scope: str, reason: str) -> dict:
        """Task/phase is over: move the entire scope's canon back into
        ordinary long-term memory in one operation. Entries are not
        forgotten or downgraded — they just stop being prioritized on
        recall. Requires a reason, logged for each affected memory."""
        scope = _require_text(scope, "scope")
        reason = _require_text(reason, "reason")
        rows = self._conn.execute(
            "SELECT * FROM canon_entries WHERE scope=? AND decommissioned_at "
            "IS NULL",
            (scope,),
        ).fetchall()
        if not rows:
            raise ValueError(f"scope {scope!r} has no active canon entries")
        now = _now()
        self._conn.executemany(
            "UPDATE canon_entries SET decommissioned_at=? WHERE id=?",
            [(now, r["id"]) for r in rows],
        )
        for r in rows:
            self._log(r["memory_id"], "end_scope", f"scope={scope}: {reason}")
        self._conn.commit()
        return {"scope": scope, "decommissioned": len(rows), "reason": reason}

    @_locked
    def list_canon(self, scope: str | None = None) -> list[dict]:
        """List active canon entries, optionally filtered by scope."""
        if scope is None:
            rows = self._conn.execute(
                "SELECT c.*, m.content, m.status, m.tier "
                "FROM canon_entries c JOIN memories m ON m.id = c.memory_id "
                "WHERE c.decommissioned_at IS NULL AND m.forgotten_at IS NULL "
                "ORDER BY c.canonized_at ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT c.*, m.content, m.status, m.tier "
                "FROM canon_entries c JOIN memories m ON m.id = c.memory_id "
                "WHERE c.decommissioned_at IS NULL AND m.forgotten_at IS NULL "
                "AND c.scope=? "
                "ORDER BY c.canonized_at ASC",
                (scope,),
            ).fetchall()
        return [dict(r) for r in rows]

    @_locked
    def active_scopes(self) -> list[str]:
        """List distinct scopes that currently have active canon entries."""
        rows = self._conn.execute(
            "SELECT DISTINCT scope FROM canon_entries WHERE decommissioned_at "
            "IS NULL ORDER BY scope"
        ).fetchall()
        return [r["scope"] for r in rows]

    # -- audit ------------------------------------------------------------------

    @_locked
    def audit_log(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- retrieval ----------------------------------------------------------

    @_locked
    def get(self, memory_id: str) -> Memory | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return Memory(**dict(row))

    @_locked
    def recall(self, query: str | None = None, limit: int = 10) -> dict:
        """Recall memories. Anchors are always returned first, in full,
        regardless of the query — they do not compete on relevance.
        Remaining slots are filled by consolidated memories, then working
        memories, newest first, optionally filtered by a naive substring
        match on `query` (v0.1/v0.2 have no embedding dependency by design).

        Also surfaces `stale_interpretations`: interpretation-tier memories
        due for review, so callers can prompt for re-examination. And
        `narrative`: the current narrative synthesis from `narrate()`, if
        one has ever been submitted, alongside the discrete memory list —
        recall gives both the story and the raw facts it was built from.
        And `canon`: the active, task-scoped canon entries (Assmann), which
        are prioritized like anchors but are expected to rotate as tasks
        change — unlike permanent anchors."""
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
            "canon": self.list_canon(),
            "memories": rest,
            "stale_interpretations": self.due_for_review(),
            "narrative": self.current_narrative(),
        }
