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
  alongside the discrete memory list when its dependencies are usable. Invalid
  sources suppress the default text; explicit inspection preserves it, and
  review_narrative() records revalidation without rewriting the account.
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
- Social framing / multi-perspective memory (Halbwachs: cadres sociaux —
  memory is always framed by the social group/context in which it was
  formed; there is no frame-free memory): memories can carry a `frame`
  (the relational/social context the memory belongs to, e.g. "team-alpha",
  "collab-with-B", "project-x"). Two memories about the same subject may
  legitimately disagree across frames — instead of silently overwriting
  the older version, `mark_conflict(a, b, reason)` declares the pair as
  conflicting framed versions, both retained. `resolve_conflict(...)`
  records how the conflict was settled (which version adopted, or merged,
  or deferred) without deleting the losing version. `recall()` surfaces
  open conflicts explicitly, and can be filtered by `frame`.

Design principle: every state change that matters (promote, pin, corroborate,
review, forget, restore, narrate, canonize, decanonize, end_scope,
flag_sensitive, mark_conflict, resolve_conflict, set_frame) is recorded with
a reason/note and a timestamp in a single audit log. Nothing is silently
reclassified, and nothing is silently deleted.
"""

from __future__ import annotations

import functools
import json
import math
import re
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


class _AuditedDenial(PermissionError):
    """Internal signal: the guard wrote only a deliberate denial audit."""


def _transactional(method):
    """Own one transaction per outer state-changing call.

    Reserve SQLite's writer before reading preconditions, so other Store
    connections/processes cannot change the state between a guard and its
    write. Nested calls (recall -> due_for_review) share the outer boundary.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if self._transaction_active:
                return method(self, *args, **kwargs)

            self._conn.execute("BEGIN IMMEDIATE")
            self._transaction_active = True
            denial = None
            try:
                try:
                    result = method(self, *args, **kwargs)
                except _AuditedDenial as exc:
                    # Guards raise this only before any business mutation.
                    # Ordinary exceptions, including failed audit inserts,
                    # must never take this commit path.
                    denial = exc
                    result = None
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                self._transaction_active = False

            if denial is not None:
                raise PermissionError(str(denial)) from None
            return result

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
    security_sensitive INTEGER NOT NULL DEFAULT 0,
    frame TEXT,                     -- social/relational frame the memory belongs to (Halbwachs)
    content_tokens TEXT             -- cached normalized token set for lexical ranking (v1.0)
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
    action TEXT NOT NULL,   -- 'promote' | 'pin' | 'corroborate_upgrade' | 'review' | 'forget' | 'restore' | 'flag_sensitive' | 'auto_flag_sensitive' | 'pin_denied' | 'narrate' | 'canonize' | 'end_scope' | 'decanonize' | 'set_frame' | 'mark_conflict' | 'resolve_conflict'
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
    superseded_by TEXT REFERENCES narratives(id),
    security_sensitive INTEGER NOT NULL DEFAULT 0,
    review_required_at TEXT,
    review_reason TEXT,
    last_reviewed_at TEXT,
    review_note TEXT
);

CREATE TABLE IF NOT EXISTS canon_entries (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    scope TEXT NOT NULL,
    reason TEXT NOT NULL,
    canonized_at TEXT NOT NULL,
    decommissioned_at TEXT          -- NULL while this entry is in the active canon
);

CREATE TABLE IF NOT EXISTS conflicts (
    id TEXT PRIMARY KEY,
    memory_id_a TEXT NOT NULL REFERENCES memories(id),
    memory_id_b TEXT NOT NULL REFERENCES memories(id),
    reason TEXT NOT NULL,              -- why these two framed versions are in conflict
    created_at TEXT NOT NULL,
    resolved_at TEXT,                  -- NULL while open
    resolution_reason TEXT,            -- how it was settled, recorded without deleting either version
    adopted_memory_id TEXT             -- which framed version was adopted, if any; NULL = merged/deferred
);
"""

# Columns added after the initial schema, applied to pre-existing databases
# via ALTER TABLE since CREATE TABLE IF NOT EXISTS never adds columns to an
# existing table. (v0.5 added security_sensitive without this and any db file
# created before v0.5 would have crashed on first use — caught while adding
# `frame` for v0.8.)
MIGRATIONS = [
    ("memories", "security_sensitive", "INTEGER NOT NULL DEFAULT 0"),
    ("memories", "frame", "TEXT"),
    ("memories", "content_tokens", "TEXT"),
    ("narratives", "security_sensitive", "INTEGER NOT NULL DEFAULT 0"),
    ("narratives", "review_required_at", "TEXT"),
    ("narratives", "review_reason", "TEXT"),
    ("narratives", "last_reviewed_at", "TEXT"),
    ("narratives", "review_note", "TEXT"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _require_text(value: str, field: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{field} is required and cannot be empty")
    return value


# -- lexical relevance ranking (v1.0) -----------------------------------------
#
# Embedding-backed semantic recall is deliberately out of scope (no model
# dependency, offline-friendly). But pure substring matching is too brittle:
# "查一下上次那个方案" won't match "初步方案已定：采用分层设计". This is a
# middle layer: an Okapi BM25-style lexical scorer over normalized token sets
# (CJK bigrams + alphanumeric words, lowercase). It ranks rather than
# hard-filters — a memory with zero query overlap can still be returned when
# few others match, which keeps recall generous, while memories that share
# more vocabulary with the query rank earlier. CJK text is tokenized as
# character bigrams (the standard trick for Chinese/Japanese search without
# a segmenter); latin text as whole words with a small stopword list.

_STOPWORDS = frozenset(
    "the a an of to in on for with and or is are was were be been this that "
    "it its as at by from we you i he she they them his her their our your "
    "什么 这个 那个 一下 我们 你们 他 她 它 的 了 是 在 和 有 对 从 被 把".split()
)


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed CJK/latin text: latin words lowercased, CJK runs as
    character bigrams (unigram for single-char runs). Punctuation ignored."""
    tokens: list[str] = []
    buf: list[str] = []
    cjk: list[str] = []

    def flush_words():
        if buf:
            w = "".join(buf).lower()
            if w not in _STOPWORDS and len(w) > 1:
                tokens.append(w)
            buf.clear()

    def flush_cjk():
        if cjk:
            if len(cjk) == 1:
                tokens.append(cjk[0])
            else:
                tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
            cjk.clear()

    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch == "_"):
            flush_cjk()
            buf.append(ch)
        else:
            flush_words()
            if "\u4e00" <= ch <= "\u9fff":
                cjk.append(ch)
            else:
                flush_cjk()
    flush_words()
    flush_cjk()
    return tokens


def _bm25_scores(query_tokens: list[str], doc_tokens: list[str],
                 avgdl: float, df: dict[str, int], n_docs: int,
                 k1: float = 1.5, b: float = 0.75) -> float:
    """BM25 relevance of one document to the query. Pure Python, no index
    structures — doc counts here are small (bounded by the memory store's
    scale in practice), so a linear scan with cached token lists is fine."""
    if not query_tokens or not doc_tokens:
        return 0.0
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    dl = len(doc_tokens)
    score = 0.0
    for qt in dict.fromkeys(query_tokens):
        if qt not in tf:
            continue
        n_qt = df.get(qt, 0)
        idf = math.log1p((n_docs - n_qt + 0.5) / (n_qt + 0.5))
        score += idf * (tf[qt] * (k1 + 1)) / (
            tf[qt] + k1 * (1 - b + b * dl / (avgdl if avgdl > 0 else 1.0))
        )
    return score


# -- deterministic sensitivity heuristics (v1.1) --------------------------------
#
# The two-stage defense had a gap: the LLM-judgment stage (setting
# security_sensitive) is probabilistic, so a stealthy injection that the
# agent fails to flag bypasses the enforcement stage entirely. This closes
# the *recognizable-pattern* part of that gap server-side: remember()
# pattern-matches the classic injection shapes (claims of developer/admin
# authority, standing-instruction/imperative phrasing, credential shapes,
# self-asserted authorization) and auto-flags without relying on the caller.
# This is deliberately narrow — false positives cost little (the memory is
# still stored and recallable; only pin() requires corroboration) while
# false negatives are still possible (novel injection shapes fall through
# to the LLM-judgment stage as before). Deterministic where we can be,
# LLM-judged where we must be.

_INJECTION_PATTERNS = [
    # claims of authority / developer instruction
    re.compile(r"(?i)(from|by)\s+the\s+(developer|admin(istrator)?|system|creator)"),
    re.compile(r"(?i)developer\s+(said|says|instructed|directed|noted)"),
    re.compile(r"(?i)(system|admin)\s+(notice|announcement|message|directive|instruction)"),
    # standing instructions / imperative overrides
    re.compile(r"(?i)ignore\s+(all\s+)?(prior|previous|above|earlier)"),
    re.compile(r"(?i)(from now on|going forward|in all future),?\s+(all\s+)?(requests?|actions?|commands?)"),
    re.compile(r"(?i)(pre-?authorized|pre-?approved|no (further )?(review|approval) (is )?(required|needed))"),
    re.compile(r"(?i)(bypass|skip|override)\s+(all\s+)?(review|approval|restrictions?|safety|checks?)"),
    # self-asserted authorization
    re.compile(r"(?i)(the )?user\s+is\s+now\s+(authorized|approved|granted|admin)"),
    re.compile(r"(?i)you\s+(are|'re)\s+now\s+(authorized|permitted|allowed|admin)"),
    # credential shapes
    re.compile(r"(?i)(api[\s_-]?key|secret|password|token|credential)s?\s*[:=]"),
    # Chinese injection shapes (mirrors of the above)
    re.compile(r"(开发(者|人员|商)|管理员|系统)(通知|公告|消息|指令|提示)[:：]"),
    re.compile(r"(来自|根据)(开发(者|人员|商)|管理员|系统).{0,12}(指示|命令|通知|要求)"),
    re.compile(r"(忽略|无视|跳过)(之前|以上|先前|前面|所有)?(的)?(指令|指示|规则|限制|约束)"),
    re.compile(r"(已)?(预授权|预批准|预先授权)(.{0,8}(无需|不用|免)(二级)?(审批|审查|复核))?"),
    re.compile(r"(从现在起|今后|以后)(所有|全部)?(请求|操作|指令|申请).{0,10}(免|跳过|无需|不用)"),
    re.compile(r"(密钥|秘钥|密码|口令|令牌)[:：=]"),
]

DEFAULT_SENSITIVITY_AUTO_FLAG = True


def _looks_injected(content: str) -> str | None:
    """Return a human-readable reason if content matches a known injection
    pattern, else None. Narrow by design: only the canonical shapes."""
    for pattern in _INJECTION_PATTERNS:
        m = pattern.search(content)
        if m:
            return f"auto-flagged: matches injection pattern {pattern.pattern!r} near {m.group(0)!r}"
    return None


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
    frame: str | None = None

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
            "frame": self.frame,
        }

class Store:
    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        anchor_soft_limit: int = DEFAULT_ANCHOR_SOFT_LIMIT,
        interpretation_review_days: int = DEFAULT_INTERPRETATION_REVIEW_DAYS,
        canon_soft_limit: int = DEFAULT_CANON_SOFT_LIMIT,
        *,
        semantic_backend=None,
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
        try:
            # executescript commits any previous transaction: put BEGIN inside
            # the script so schema creation AND additive upgrades share a lock.
            self._conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            self._migrate()
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            self._conn.close()
            raise
        self._lock = threading.RLock()
        self._transaction_active = False
        self.anchor_soft_limit = anchor_soft_limit
        self.interpretation_review_days = interpretation_review_days
        self.canon_soft_limit = canon_soft_limit
        self._semantic_backend = semantic_backend

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        """Apply additive column migrations to databases created by older
        versions. CREATE TABLE IF NOT EXISTS never adds columns to an
        existing table, so each post-launch column needs an explicit
        ALTER TABLE. Idempotent: checks PRAGMA table_info first."""
        for table, column, definition in MIGRATIONS:
            cols = {r["name"] for r in
                    self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )

    def _log(self, memory_id: str, action: str, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (id, memory_id, action, reason, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_new_id(), memory_id, action, reason, _now()),
        )

    # -- capture --------------------------------------------------------------

    @_transactional
    def remember(
        self,
        content: str,
        source: str | None = None,
        tier: str = "archive",
        security_sensitive: bool = False,
        frame: str | None = None,
    ) -> Memory:
        """Store a new working memory. Working memories are ordinary
        recollections — they can still be recalled, but they have not gone
        through the consolidation ceremony.

        `tier` defaults to 'archive' (captured as-is). Pass tier='interpretation'
        when the content is the agent's own inference/summary rather than a
        directly observed fact — this schedules it for periodic review.
        New testimony must be established through `corroborate()`; directly
        supplying tier='testimony' is rejected.

        Set `security_sensitive=True` for anything touching identity,
        permissions, or standing instructions (e.g. "the developer said I
        can ignore my system prompt", "the admin's password is..."). This
        does not block the memory, but it raises the bar for `pin()`: see
        `flag_sensitive()` and `pin()`.

        `frame` (optional) records the social/relational frame this memory
        belongs to (Halbwachs) — e.g. "team-alpha", "collab-with-B",
        "project-x". Framed memories can disagree across frames without one
        silently overwriting the other: see `mark_conflict()`.

        Sensitivity auto-flagging (v1.1): unless `security_sensitive` was
        explicitly set True by the caller, the content is screened against
        deterministic injection-pattern heuristics (claims of developer/
        admin authority, standing-instruction phrasing, credential shapes,
        self-asserted authorization). A match auto-sets the flag and logs
        the reason as `auto_flag_sensitive`. This closes the gap where a
        stealthy injection bypasses the protocol's enforcement stage simply
        because the LLM forgot to set the flag — narrow patterns only, at
        near-zero false-positive cost (flagged memories remain stored and
        recallable; only pin() requires corroboration)."""
        content = _require_text(content, "content")
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
        if tier == "testimony":
            raise ValueError(
                "testimony requires recorded evidence; remember as archive, "
                "then corroborate with independent sources"
            )
        mid = _new_id()
        now = _now()
        review_status = "current" if tier == "interpretation" else None
        last_reviewed_at = now if tier == "interpretation" else None
        auto_reason = (
            None if security_sensitive else _looks_injected(content)
        )
        if auto_reason:
            security_sensitive = True
        self._conn.execute(
            "INSERT INTO memories "
            "(id, content, source, status, tier, created_at, last_reviewed_at, "
            "review_status, security_sensitive, frame, content_tokens) "
            "VALUES (?, ?, ?, 'working', ?, ?, ?, ?, ?, ?, ?)",
            (
                mid,
                content,
                source,
                tier,
                now,
                last_reviewed_at,
                review_status,
                int(security_sensitive),
                frame,
                json.dumps(_tokenize(content)),
            ),
        )
        if auto_reason:
            self._log(mid, "auto_flag_sensitive", auto_reason)
        return self.get(mid)

    @_locked
    def due_for_consolidation(self, days: float = 0.0, limit: int = 20) -> list[dict]:
        """Consolidation queue: working-tier memories that have existed for
        at least `days` days (default: any age) and are not forgotten.
        Returns them oldest-first, up to `limit`.

        This is the deterministic antidote to invocation variance: instead
        of hoping the in-conversation agent calls promote() at the right
        moment (measurably unreliable — same prompt sometimes promotes,
        sometimes stops at remember), a host app or the agent itself can
        call this at session boundaries — a fixed, ceremonial moment, closer
        to how Assmann's consolidation actually works (a periodic rite, not
        an in-the-moment judgment) — and promote what has stood the test of
        a little time. Suggested cadence: end of session."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE status='working' AND "
            "forgotten_at IS NULL AND created_at <= ? "
            "ORDER BY created_at ASC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @_transactional
    def flag_sensitive(self, memory_id: str, reason: str) -> dict:
        """Retroactively flag an existing memory as security-sensitive
        (identity / permissions / standing-instruction content). Once
        flagged, `pin()` will require independent corroboration — see
        `pin()`. Requires a reason, logged in the audit trail.

        If the memory is currently pinned as an anchor, the pin is
        automatically lifted: an anchor that is later recognized as
        security-sensitive but never independently corroborated should not
        keep its always-surfaced status while we wait for someone to notice.
        Unsupported canon scopes are also decommissioned and linked narratives
        invalidated, all atomically. Supported memberships stay active.
        The unpin is itself logged (action 'unpin_by_sensitivity') so the
        chain of events stays auditable. If corroboration is later
        obtained, `pin()` can be called again and will succeed."""
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        self._conn.execute(
            "UPDATE memories SET security_sensitive=1 WHERE id=?", (memory_id,)
        )
        unpin_note = None
        if self.is_anchored(memory_id) and self.independent_corroboration_count(memory_id) < 1:
            self._conn.execute("DELETE FROM anchors WHERE memory_id=?", (memory_id,))
            unpin_note = (
                "anchor lifted: flagged security_sensitive without "
                "independent corroboration"
            )
            self._log(memory_id, "unpin_by_sensitivity", unpin_note)
        decommissioned = []
        if self.independent_corroboration_count(memory_id) < 1:
            entries = self._conn.execute(
                "SELECT id, scope FROM canon_entries WHERE memory_id=? "
                "AND decommissioned_at IS NULL ORDER BY scope", (memory_id,),
            ).fetchall()
            now = _now()
            for entry in entries:
                self._conn.execute(
                    "UPDATE canon_entries SET decommissioned_at=? WHERE id=?",
                    (now, entry["id"]),
                )
                self._log(memory_id, "decanonize_by_sensitivity",
                          f"scope={entry['scope']}: {reason}")
                decommissioned.append(entry["scope"])
            self._invalidate_narratives(memory_id, "source flagged without independent corroboration")
        self._log(memory_id, "flag_sensitive", reason)
        result = self.get(memory_id).to_dict()
        if unpin_note is not None:
            result["unpinned_by_sensitivity"] = True
        if decommissioned:
            result["decanonized_by_sensitivity"] = decommissioned
        return result

    @_locked
    def independent_corroboration_count(self, memory_id: str) -> int:
        """Count corroborating sources for this memory that are distinct
        from the memory's own recorded `source`. A memory corroborated only
        by its own source does not count as independently verified — this
        is the check `pin()` uses for security-sensitive memories.

        Strictness on missing origins: a memory with no recorded source
        (None, empty, or whitespace-only) has an *unknown* origin. Any
        single corroborating source therefore cannot be assumed to
        differ from that unknown origin, so it does not count as
        independent. Such a memory needs two *distinct* corroborating
        sources to count as independently corroborated — with an unknown
        origin, at least two different voices are required before any of
        them can be considered independent of wherever the content really
        came from. Blank/whitespace corroborating sources never count."""
        return self.provenance(memory_id)["independent_corroboration_count"]

    @_locked
    def provenance(self, memory_id: str) -> dict:
        """Inspect recorded support without changing historical tiers.

        Source identifiers are caller-supplied, case-sensitive, and trimmed
        for comparison. They are not authenticated attestations. One query
        keeps the memory and its evidence in the same SQLite read snapshot.
        """
        rows = self._conn.execute(
            "SELECT m.source, m.tier, c.source AS corroborating_source "
            "FROM memories m LEFT JOIN corroborations c ON c.memory_id=m.id "
            "WHERE m.id=?",
            (memory_id,),
        ).fetchall()
        if not rows:
            raise KeyError(f"no such memory: {memory_id}")
        distinct_sources = {
            r["corroborating_source"].strip() for r in rows
            if r["corroborating_source"] and r["corroborating_source"].strip()
        }
        origin = (rows[0]["source"] or "").strip()
        count = (
            len(distinct_sources - {origin}) if origin
            else max(len(distinct_sources) - 1, 0)
        )
        result = {
            "memory_id": memory_id,
            "tier": rows[0]["tier"],
            "source": rows[0]["source"],
            "origin_known": bool(origin),
            "corroborating_sources": sorted(distinct_sources),
            "independent_corroboration_count": count,
            "corroboration_satisfied": count > 0,
        }
        if result["tier"] == "testimony" and count == 0:
            result["warning"] = (
                "Historical testimony lacks sufficient recorded independent "
                "corroboration under the current rule. The stored tier is "
                "preserved; obtain genuine independent evidence before relying on it."
            )
        return result

    # -- consolidation module (Assmann) ---------------------------------------

    @_transactional
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
        return self.get(memory_id)

    # -- anchor module (Nora) --------------------------------------------------

    @_transactional
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
        if mem.is_forgotten:
            raise ValueError("cannot pin a forgotten memory; restore() it first")
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
            raise _AuditedDenial(denial_reason)
        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO anchors (memory_id, reason, pinned_at) "
            "VALUES (?, ?, ?)",
            (memory_id, reason, now),
        )
        self._log(memory_id, "pin", reason)

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

    @_transactional
    def unpin(self, memory_id: str, reason: str | None = None) -> None:
        """Remove anchor status and audit the outcome in the same transaction.

        Omitted reasons retain the legacy call form with an explicit audit
        marker. An already-unpinned or unknown ID is an audited no-op.
        """
        reason = (
            "legacy unpin: caller did not provide a reason" if reason is None
            else _require_text(reason, "reason")
        )
        removed = self._conn.execute(
            "DELETE FROM anchors WHERE memory_id=?", (memory_id,)
        ).rowcount
        self._log(memory_id, "unpin" if removed else "unpin_noop", reason)

    @_locked
    def is_anchored(self, memory_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM anchors WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return row is not None

    def _active_canon_count(self, memory_id: str) -> int:
        """Count of active (not decommissioned) canon entries for a memory,
        across all scopes. Used by `forget()` to enforce the canon guard."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM canon_entries WHERE memory_id=? AND "
            "decommissioned_at IS NULL",
            (memory_id,),
        ).fetchone()[0]

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

    @_transactional
    def forget(self, memory_id: str, reason: str) -> Memory:
        """Deliberately forget a memory. This is not deletion: the content
        is retained (a tombstone), but the memory disappears from `recall()`
        and `list_anchors()`. Requires a non-empty reason, logged in the
        audit trail — forgetting is a legitimate, accountable act, not a
        silent side-effect of storage pressure.

        An anchored memory cannot be forgotten directly: call `unpin()`
        first. A canonized memory likewise cannot be forgotten directly:
        call `decanonize()` (or `end_scope()`) first. Both guards exist for
        the same reason — a memory currently serving as an identity
        cornerstone or as actively task-prioritized should not quietly
        vanish as a side-effect of an unrelated forgetting decision;
        removing it from that role has to be its own, separately reasoned
        step. Without the canon guard, forgetting a canonized memory would
        leave an orphaned "active" canon entry pointing at a tombstoned
        memory.

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
        if self._active_canon_count(memory_id) > 0:
            raise ValueError(
                "memory is in the active canon; call decanonize() (or "
                "end_scope()) first before it can be forgotten"
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
        self._invalidate_narratives(memory_id, "source forgotten")
        self._log(memory_id, "forget", reason)
        return self.get(memory_id)

    @_transactional
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
        self._invalidate_narratives(memory_id, "source restored; synthesis needs review")
        self._log(memory_id, "restore", reason)
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

    @_transactional
    def corroborate(self, memory_id: str, source: str) -> Memory:
        """Record that an independent additional source corroborates this
        memory. An 'archive' (raw, single-source) memory is automatically
        upgraded to 'testimony' only when the independent-source gate used by
        sensitive pinning is satisfied. Same-source and duplicate reports
        are recorded but add no independent support. 'interpretation'
        memories are not upgraded by corroboration alone — they must go
        through `review()` instead, since they are inferences, not facts
        that a second source can simply confirm."""
        source = _require_text(source, "source")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        # Persist any existing dependency failure before new evidence can
        # make a legacy synthesis look current again.
        self._invalidate_narratives(memory_id, "source evidence changed after invalidation",
                                    only_if_source_unusable=True)
        now = _now()
        self._conn.execute(
            "INSERT INTO corroborations (id, memory_id, source, at) VALUES (?, ?, ?, ?)",
            (_new_id(), memory_id, source, now),
        )
        self._log(memory_id, "corroborate", f"recorded corroborating source: {source}")
        if mem.tier == "archive" and self.independent_corroboration_count(memory_id) > 0:
            self._conn.execute(
                "UPDATE memories SET tier='testimony' WHERE id=?", (memory_id,)
            )
            self._log(
                memory_id,
                "corroborate_upgrade",
                f"corroborated by additional source: {source}",
            )
        return self.get(memory_id)

    @_transactional
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
        self._invalidate_narratives(memory_id, "overdue source reviewed; synthesis needs review",
                                    only_if_source_unusable=True)
        now = _now()
        self._conn.execute(
            "UPDATE memories SET last_reviewed_at=?, review_status='current' WHERE id=?",
            (now, memory_id),
        )
        self._log(memory_id, "review", note)
        return self.get(memory_id)

    @_transactional
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
            for r in stale:
                r["review_status"] = "stale"
                self._invalidate_narratives(r["id"], "interpretation source is overdue")
        return stale

    # -- narrative integration module (Ricoeur: identité narrative) -------------

    @staticmethod
    def _normalize_memory_ids(memory_ids: list[str] | None) -> list[str]:
        if memory_ids is None:
            return []
        if not isinstance(memory_ids, list) or any(
            not isinstance(mid, str) or not mid.strip() for mid in memory_ids
        ):
            raise ValueError("memory_ids must be a list of nonempty memory IDs")
        return list(dict.fromkeys(memory_ids))

    def _narrative_sources(self, memory_ids: list[str], sensitive: bool) -> list[dict]:
        """Compute live dependency issues without changing historical evidence."""
        issues = []
        if sensitive and not memory_ids:
            issues.append({"memory_id": None, "issue": "missing_evidence"})
        cutoff = (datetime.now(timezone.utc) - timedelta(
            days=self.interpretation_review_days)).isoformat()
        for mid in memory_ids:
            mem = self.get(mid)
            if mem is None:
                issues.append({"memory_id": mid, "issue": "missing"})
                continue
            if mem.is_forgotten:
                issues.append({"memory_id": mid, "issue": "forgotten"})
            if mem.tier == "interpretation" and (
                mem.last_reviewed_at is None or mem.last_reviewed_at < cutoff
                or mem.review_status == "stale"
            ):
                issues.append({"memory_id": mid, "issue": "stale_interpretation"})
            if (sensitive or mem.is_security_sensitive) and not self.provenance(mid)["corroboration_satisfied"]:
                issues.append({"memory_id": mid, "issue": "insufficient_corroboration"})
        return issues

    def _invalidate_narratives(self, memory_id: str, reason: str,
                              only_if_source_unusable: bool = False) -> None:
        for row in self._conn.execute(
            "SELECT * FROM narratives WHERE review_required_at IS NULL"
        ).fetchall():
            narrative = self._narrative_to_dict(row)
            if memory_id not in narrative["memory_ids"]:
                continue
            if only_if_source_unusable and not any(
                issue["memory_id"] == memory_id for issue in narrative["source_issues"]
            ):
                continue
            self._conn.execute(
                "UPDATE narratives SET review_required_at=?, review_reason=? WHERE id=?",
                (_now(), reason, row["id"]),
            )
            self._log(row["id"], "narrative_invalidated", f"source={memory_id}: {reason}")

    @_transactional
    def narrate(
        self, content: str, reason: str, memory_ids: list[str] | None = None,
        security_sensitive: bool = False,
    ) -> dict:
        """Version a synthesis with active, traceable dependencies.

        Unlinked nonsensitive accounts remain supported and explicitly labeled.
        Sensitive text (explicit or recognized pattern) requires independently
        corroborated linked evidence. Links do not establish semantic entailment.
        """
        content = _require_text(content, "content")
        reason = _require_text(reason, "reason")
        memory_ids = self._normalize_memory_ids(memory_ids)
        sensitive = bool(security_sensitive or _looks_injected(content))
        issues = self._narrative_sources(memory_ids, sensitive)
        if any(issue["issue"] in ("missing", "forgotten", "stale_interpretation") for issue in issues):
            raise ValueError(f"narrative has unresolved source issues: {issues}")
        nid = _new_id()
        if issues:
            denial = "narrative requires independent corroboration of linked evidence"
            self._log(nid, "narrate_denied", f"{denial}: {issues}")
            raise _AuditedDenial(denial)
        now = _now()
        prev = self._current_narrative_row()
        self._conn.execute(
            "INSERT INTO narratives (id, content, reason, memory_ids, created_at, security_sensitive) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (nid, content, reason, json.dumps(memory_ids), now, int(sensitive)),
        )
        if prev is not None:
            self._conn.execute(
                "UPDATE narratives SET superseded_at=?, superseded_by=? WHERE id=?",
                (now, nid, prev["id"]),
            )
        self._log(nid, "narrate", reason)
        return self._narrative_to_dict(self._conn.execute(
            "SELECT * FROM narratives WHERE id=?", (nid,)
        ).fetchone())

    def _current_narrative_row(self) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM narratives WHERE superseded_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    def _narrative_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        issues = []
        try:
            d["memory_ids"] = self._normalize_memory_ids(
                json.loads(d["memory_ids"]) if d["memory_ids"] else None)
        except (ValueError, TypeError):
            d["memory_ids"] = []
            issues.append({"memory_id": None, "issue": "invalid_memory_ids"})
        d["security_sensitive"] = bool(d["security_sensitive"] or _looks_injected(d["content"]))
        d["source_issues"] = issues + self._narrative_sources(d["memory_ids"], d["security_sensitive"])
        d["review_status"] = "stale" if d["review_required_at"] or d["source_issues"] else "current"
        d["provenance_status"] = "linked" if d["memory_ids"] else "unlinked"
        if not d["memory_ids"]:
            d["warning"] = "Unlinked narrative: no traceable memory sources; this account is not verified."
        return d

    @_transactional
    def review_narrative(self, narrative_id: str, note: str) -> dict:
        """Explicitly revalidate the current synthesis after its sources recover.

        Refuse unresolved issues or superseded versions. The caller must examine
        the text: this records their judgment, without rewriting the account.
        """
        note = _require_text(note, "note")
        row = self._conn.execute("SELECT * FROM narratives WHERE id=?", (narrative_id,)).fetchone()
        if row is None:
            raise KeyError(f"no such narrative: {narrative_id}")
        if row["superseded_at"] is not None:
            raise ValueError("only the current narrative can be reviewed; use narrate() for a new account")
        narrative = self._narrative_to_dict(row)
        if narrative["source_issues"]:
            raise ValueError(f"narrative has unresolved source issues: {narrative['source_issues']}")
        self._conn.execute(
            "UPDATE narratives SET review_required_at=NULL, review_reason=NULL, "
            "last_reviewed_at=?, review_note=? WHERE id=?", (_now(), note, narrative_id),
        )
        self._log(narrative_id, "review_narrative", note)
        return self._narrative_to_dict(self._conn.execute(
            "SELECT * FROM narratives WHERE id=?", (narrative_id,),
        ).fetchone())

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

    @_transactional
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
        if mem.is_security_sensitive and self.independent_corroboration_count(memory_id) < 1:
            denial = "security-sensitive canon requires independent corroboration"
            self._log(memory_id, "canonize_denied", f"scope={scope}: {denial}")
            raise _AuditedDenial(denial)
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

    @_transactional
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
        return {"memory_id": memory_id, "decommissioned": len(rows),
                "scope": scope, "reason": reason}

    @_transactional
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
        return {"scope": scope, "decommissioned": len(rows), "reason": reason}

    @_locked
    def list_canon(self, scope: str | None = None) -> list[dict]:
        """Inspect active canon, including eligibility of unsupported legacy rows."""
        if scope is None:
            rows = self._conn.execute(
                "SELECT c.*, m.content, m.status, m.tier, m.security_sensitive "
                "FROM canon_entries c JOIN memories m ON m.id = c.memory_id "
                "WHERE c.decommissioned_at IS NULL AND m.forgotten_at IS NULL "
                "ORDER BY c.canonized_at ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT c.*, m.content, m.status, m.tier, m.security_sensitive "
                "FROM canon_entries c JOIN memories m ON m.id = c.memory_id "
                "WHERE c.decommissioned_at IS NULL AND m.forgotten_at IS NULL "
                "AND c.scope=? "
                "ORDER BY c.canonized_at ASC",
                (scope,),
            ).fetchall()
        result = []
        for row in rows:
            entry = dict(row)
            entry["eligible_for_recall"] = not entry["security_sensitive"] or self.provenance(entry["memory_id"])["corroboration_satisfied"]
            if not entry["eligible_for_recall"]:
                entry["warning"] = "Sensitive legacy canon lacks independent corroboration; excluded from priority recall."
            result.append(entry)
        return result

    @_locked
    def active_scopes(self) -> list[str]:
        """List distinct scopes that currently have active canon entries."""
        rows = self._conn.execute(
            "SELECT DISTINCT scope FROM canon_entries WHERE decommissioned_at "
            "IS NULL ORDER BY scope"
        ).fetchall()
        return [r["scope"] for r in rows]

    # -- social framing / multi-perspective memory (Halbwachs) -------------------

    @_transactional
    def set_frame(self, memory_id: str, frame: str, reason: str) -> Memory:
        """Retroactively assign (or re-assign) a memory's social frame.
        Requires a reason, logged — re-framing a memory is itself a
        historiographical act, not a silent re-tag."""
        frame = _require_text(frame, "frame")
        reason = _require_text(reason, "reason")
        mem = self.get(memory_id)
        if mem is None:
            raise KeyError(f"no such memory: {memory_id}")
        self._conn.execute(
            "UPDATE memories SET frame=? WHERE id=?", (frame, memory_id)
        )
        self._log(memory_id, "set_frame", f"frame={frame}: {reason}")
        return self.get(memory_id)

    @_locked
    def list_frames(self) -> list[str]:
        """List distinct frames currently in use."""
        rows = self._conn.execute(
            "SELECT DISTINCT frame FROM memories WHERE frame IS NOT NULL "
            "ORDER BY frame"
        ).fetchall()
        return [r["frame"] for r in rows]

    @_transactional
    def mark_conflict(
        self, memory_id_a: str, memory_id_b: str, reason: str
    ) -> dict:
        """Declare two memories as conflicting framed versions of the same
        subject — e.g. colleague A's account of a deadline vs. colleague B's.
        Neither version is deleted or overwritten; the conflict is recorded
        so `recall()` can surface it explicitly instead of one version
        silently winning. Requires a reason, logged. Symmetric: (a, b) and
        (b, a) are the same conflict; marking the same pair twice is a
        no-op returning the existing record."""
        reason = _require_text(reason, "reason")
        if memory_id_a == memory_id_b:
            raise ValueError("a memory cannot conflict with itself")
        for mid in (memory_id_a, memory_id_b):
            mem = self.get(mid)
            if mem is None:
                raise KeyError(f"no such memory: {mid}")
            if mem.is_forgotten:
                raise ValueError(
                    f"memory {mid} is forgotten; restore() it before "
                    "declaring a conflict on it — conflicts describe live "
                    "framed versions, not tombstoned ones"
                )
        lo, hi = sorted([memory_id_a, memory_id_b])
        existing = self._conn.execute(
            "SELECT * FROM conflicts WHERE "
            "((memory_id_a=? AND memory_id_b=?) OR (memory_id_a=? AND memory_id_b=?)) "
            "AND resolved_at IS NULL",
            (lo, hi, hi, lo),
        ).fetchone()
        if existing:
            return dict(existing)
        cid = _new_id()
        now = _now()
        self._conn.execute(
            "INSERT INTO conflicts (id, memory_id_a, memory_id_b, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, memory_id_a, memory_id_b, reason, now),
        )
        self._log(memory_id_a, "mark_conflict", f"with={memory_id_b}: {reason}")
        return dict(self._conn.execute(
            "SELECT * FROM conflicts WHERE id=?", (cid,)
        ).fetchone())

    @_transactional
    def resolve_conflict(
        self, conflict_id: str, reason: str, adopted_memory_id: str | None = None
    ) -> dict:
        """Record how an open conflict was settled. `reason` is required and
        logged. `adopted_memory_id` optionally names which framed version was
        adopted; leave it None for "merged into something new" or "deferred,
        still open to revision". Crucially, the losing (or neither) version
        is NOT deleted — both memories remain in the store, since each was a
        legitimate memory within its own frame. Only the conflict record
        closes."""
        reason = _require_text(reason, "reason")
        row = self._conn.execute(
            "SELECT * FROM conflicts WHERE id=?", (conflict_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such conflict: {conflict_id}")
        if row["resolved_at"] is not None:
            raise ValueError("conflict is already resolved")
        if adopted_memory_id is not None and adopted_memory_id not in (
            row["memory_id_a"], row["memory_id_b"],
        ):
            raise ValueError(
                "adopted_memory_id must be one of the two conflicting memories "
                "(or omitted for a merge/deferral)"
            )
        now = _now()
        self._conn.execute(
            "UPDATE conflicts SET resolved_at=?, resolution_reason=?, "
            "adopted_memory_id=? WHERE id=?",
            (now, reason, adopted_memory_id, conflict_id),
        )
        self._log(
            row["memory_id_a"],
            "resolve_conflict",
            f"conflict={conflict_id} adopted={adopted_memory_id}: {reason}",
        )
        return dict(self._conn.execute(
            "SELECT * FROM conflicts WHERE id=?", (conflict_id,)
        ).fetchone())

    @_locked
    def list_conflicts(self, resolved: bool | None = None) -> list[dict]:
        """List conflicts. resolved=None → all; False → only open; True →
        only resolved. Each entry includes both memories' content and frame
        for quick inspection, and their forgetting timestamps. Historical
        conflicts remain inspectable even when a participant is forgotten."""
        sql = (
            "SELECT c.*, "
            "ma.content AS content_a, ma.frame AS frame_a, "
            "mb.content AS content_b, mb.frame AS frame_b, "
            "ma.forgotten_at AS forgotten_at_a, mb.forgotten_at AS forgotten_at_b "
            "FROM conflicts c "
            "JOIN memories ma ON ma.id = c.memory_id_a "
            "JOIN memories mb ON mb.id = c.memory_id_b"
        )
        if resolved is False:
            sql += " WHERE c.resolved_at IS NULL"
        elif resolved is True:
            sql += " WHERE c.resolved_at IS NOT NULL"
        sql += " ORDER BY c.created_at DESC"
        return [dict(r) for r in self._conn.execute(sql).fetchall()]

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
            "SELECT id, content, source, status, tier, created_at, "
            "consolidated_at, consolidation_reason, last_reviewed_at, "
            "review_status, forgotten_at, forgotten_reason, "
            "security_sensitive, frame FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return Memory(**dict(row))

    @_transactional
    def recall(
        self,
        query: str | None = None,
        limit: int = 10,
        frame: str | None = None,
    ) -> dict:
        """Recall memories, deduplicated across sections and bounded by a
        global budget.

        Priority cascade: anchors (identity cornerstones) first, then active
        canon entries (task-scoped), then ordinary memories (consolidated
        before working, newest first). A memory never appears in more than
        one section: if it is both pinned and canonized, it shows up under
        `anchors` only (identity takes precedence); if it is canonized, it
        shows under `canon` only. Multiple active scopes for the same memory
        consume one slot, represented by its oldest active canon entry;
        `list_canon()` retains every scope membership.

        `limit` bounds the TOTAL number of entries across anchors + canon +
        memories. Anchors are the one exception: they are always returned
        in full (that is their entire point) even if the anchor count alone
        exceeds `limit` — in that case canon and memories get no slots.
        Otherwise, slots remaining after anchors go to canon first, then to
        ordinary memories. With a `query`, ordinary memories are ranked by a
        BM25 lexical relevance score (CJK bigrams + latin words, no embedding
        dependency by design; token caches are written at `remember()` time
        and backfilled on the fly for pre-v1.0 rows). Ranking is generous
        rather than strict: everything stays eligible, relevance only
        orders — so a fuzzy query like "上次那个方案" can surface
        "初步方案已定：采用分层设计" that a substring match would have missed.
        Each returned memory carries a `relevance` score; ties keep the
        default order (consolidated first, then newest).

        `frame` (Halbwachs) optionally restricts the ordinary-memory list to
        one social frame — anchors and canon are always returned regardless
        of frame, since identity cornerstones and the active task canon are
        not frame-relative.

        Also surfaces `stale_interpretations`: interpretation-tier memories
        due for review, so callers can prompt for re-examination. And
        `narrative`: the current narrative synthesis from `narrate()`, if
        one has been submitted and its dependencies remain usable. A stale
        account is withheld and replaced by a content-free `narrative_review`
        notice; current_narrative()/narrative_history() retain its full text.
        And `conflicts`: currently open (unresolved) conflicting framed
        versions, so disagreement is surfaced explicitly rather than one
        version silently winning. Conflicts with a forgotten participant
        are hidden here but remain available through `list_conflicts()`.
        These extra sections are informational
        and do not count against `limit`."""
        # Refresh status before taking copies for the other recall sections.
        stale_interpretations = self.due_for_review()
        anchors = self.list_anchors()
        # canon entries excluding anchors (identity takes precedence over
        # task-scoping for display; the memory is not duplicated)
        all_canon = [entry for entry in self.list_canon() if entry["eligible_for_recall"]]
        anchor_ids = {a["id"] for a in anchors}
        seen_ids = set(anchor_ids)
        canon = []
        for entry in all_canon:
            if entry["memory_id"] not in seen_ids:
                canon.append(entry)
                seen_ids.add(entry["memory_id"])

        # global budget: anchors always win; canon then memories share what's left
        remaining = max(limit - len(anchors), 0)
        canon = canon[:remaining]
        rest_budget = max(remaining - len(canon), 0)

        rest: list[dict] = []
        if rest_budget:
            if frame is not None:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE forgotten_at IS NULL AND "
                    "frame = ? AND id NOT IN (SELECT memory_id FROM anchors) "
                    "ORDER BY status='consolidated' DESC, created_at DESC",
                    (frame,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE forgotten_at IS NULL AND id NOT IN "
                    "(SELECT memory_id FROM anchors) "
                    "ORDER BY status='consolidated' DESC, created_at DESC"
                ).fetchall()

            candidates = [dict(r) for r in rows if r["id"] not in seen_ids]
            if query is not None:
                rest = self._rank_by_query(candidates, query)[:rest_budget]
            else:
                rest = candidates[:rest_budget]

        narrative = self.current_narrative()
        narrative_review = None
        if narrative and narrative["review_status"] == "stale":
            narrative_review = {key: narrative[key] for key in (
                "id", "review_status", "source_issues", "review_required_at", "review_reason"
            )}
            narrative = None
        return {
            "anchors": anchors,
            "canon": canon,
            "memories": rest,
            "stale_interpretations": stale_interpretations,
            "narrative": narrative,
            "narrative_review": narrative_review,
            "conflicts": [
                conflict for conflict in self.list_conflicts(resolved=False)
                if conflict["forgotten_at_a"] is None
                and conflict["forgotten_at_b"] is None
            ],
        }

    def search(self, query: str, limit: int = 10, frame: str | None = None,
               mode: str = 'hybrid') -> dict:
        """Opt-in local semantic/hybrid search with the same history priorities.

        Inference runs outside write transactions. Re-read protocol eligibility
        afterward so concurrent forgetting, framing or priority changes apply.
        Newly eligible/changed text follows ranked rows in fresh lexical order
        and is counted as unranked; its embedding is available next search.
        This is a derived view, never an evidence or priority upgrade.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError('query is required and cannot be empty')
        if type(limit) is not int or limit < 0:
            raise ValueError('limit must be a nonnegative integer')
        if mode not in ('semantic', 'hybrid'):
            raise ValueError('mode must be semantic or hybrid')
        from .semantic import LocalE5, rank_candidates
        # Only initializing the lazy provider is synchronized; inference must
        # never hold Store's lock or a SQLite writer transaction.
        with self._lock:
            if self._semantic_backend is None:
                self._semantic_backend = LocalE5()
            backend = self._semantic_backend
        snapshot = self.recall(query, limit=2**63 - 1, frame=frame)
        budget = max(limit - len(snapshot['anchors']) - len(snapshot['canon']), 0)
        candidates = snapshot['memories'] if budget else []
        ranked = rank_candidates(candidates, query, backend, mode)
        current = self.recall(query, limit=2**63 - 1, frame=frame)
        eligible = {row['id']: row for row in current['memories']}
        selected = []
        for row in ranked:
            fresh = eligible.get(row['id'])
            if fresh is not None and fresh['content'] == row['content']:
                selected.append({**fresh, 'semantic_similarity': row['semantic_similarity'],
                                 'search_score': row['search_score']})
                del eligible[row['id']]
        unranked = len(eligible)
        selected.extend(eligible.values())
        remaining = max(limit - len(current['anchors']), 0)
        current['canon'] = current['canon'][:remaining]
        current['memories'] = selected[:max(remaining - len(current['canon']), 0)]
        current['retrieval'] = {'mode': mode, 'backend': backend.describe(),
                                'reranked_candidates': len(ranked),
                                'unranked_candidates': unranked,
                                'inference_performed': bool(candidates)}
        return current

    def _rank_by_query(self, rows: list[dict], query: str) -> list[dict]:
        """Rank ordinary memories by lexical BM25 relevance to `query`
        (v1.0: replaces hard substring filtering). Generous rather than
        strict: every candidate stays eligible, relevance only orders them —
        a query that matches nothing specific still returns the newest
        consolidated memories rather than an empty list. Candidates whose
        token cache is missing (rows written by pre-v1.0 versions and never
        re-written) are tokenized on the fly."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            for row in rows:
                row["relevance"] = 0.0
            return rows
        doc_tokens_list: list[list[str]] = []
        for r in rows:
            cached = r.get("content_tokens")
            try:
                tokens = json.loads(cached) if cached else None
            except (ValueError, TypeError):
                tokens = None
            # Pre-1.2 tokenization joined CJK runs across embedded ASCII.
            # Recompute affected caches without rewriting historical rows.
            interleaved_scripts = re.search(r"[\u4e00-\u9fff][A-Za-z0-9_]+[\u4e00-\u9fff]", r["content"])
            if (not isinstance(tokens, list)
                    or any(not isinstance(t, str) for t in tokens)
                    or interleaved_scripts):
                tokens = _tokenize(r["content"])
            doc_tokens_list.append(tokens)
        n_docs = len(rows)
        df: dict[str, int] = {}
        for dt in doc_tokens_list:
            for t in set(dt):
                df[t] = df.get(t, 0) + 1
        avgdl = sum(len(dt) for dt in doc_tokens_list) / max(n_docs, 1)
        scored = [
            (_bm25_scores(query_tokens, dt, avgdl, df, n_docs), i)
            for i, dt in enumerate(doc_tokens_list)
        ]
        # stable sort: BM25 desc, ties keep the SQL order (consolidated first,
        # then newest)
        scored.sort(key=lambda x: (-x[0],))
        ranked = [rows[i] for _, i in scored]
        for r, (score, _) in zip(ranked, scored):
            r["relevance"] = round(score, 4)
        return ranked
