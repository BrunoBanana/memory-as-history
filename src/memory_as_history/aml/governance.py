"""Deterministic memory governance for the AML evaluation stream (v0.2).

Two mechanisms, both deterministic and model-free:

1. Write-time update detection (``record_possible_updates``): after a memory
   is inserted, recent memories of the same user are scanned for same-fact
   revisions — strong token overlap (Jaccard) or near-full coverage of the
   older row by the newer statement. Each detected revision is recorded as a
   ``superseded_by`` edge in ``aml_updates`` and every decision is written to
   ``audit_log``. Nothing is deleted or rewritten: the history stays intact
   and auditable, which is the project's core philosophy.

2. Read-time version suppression (``govern_entries``): within the returned
   evidence window, near-duplicate entries are deduplicated (keep the newer)
   and older versions of the same fact are score-discounted. Suppression is
   deliberate and bounded: an older version stays present — history questions
   ("where did X used to live?") can still retrieve it — it simply ranks
   below the current version. All decisions are audited.

Design notes:

- Search responses must stay contract-clean: exactly
  ``{"id", "content", "score"?, "created_at"?}`` per entry. Governance traces
  live in ``audit_log``, never in the payload.
- Top K is the global budget. Discounting never removes an entry that would
  have been returned; only exact/near-exact duplicates are dropped, so recall
  for history-style questions is preserved.
- Worst case is O(n^2) on the *returned* window (<= official Top K) at read
  time, and O(recent_window) at write time.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..storage import _tokenize

# Similarity signals. CJK bigrams make raw Jaccard decay fast as sentences
# grow, so we combine two signals:
#   - Jaccard:  overlap / union (punishes long additions)
#   - coverage: overlap / len(old) — how much of the OLD fact the new
#     statement contains. A revision typically covers (almost) all of the
#     old tokens ("住在福州" ⊆ "住在福州，后来搬到上海").
# Duplicate merging never uses these: identical content is recognized by
# ``_normalize`` equality, because tokenization drops single digits ("第8条"
# and "第9条" share tokens but are distinct facts).
_VERSION_JACCARD = 0.50    # same-fact revisions: discount the older one
_COVERAGE_REVISION = 0.70  # old fact mostly contained in the new statement
_STRONG_JACCARD = 0.60     # strong overlap: strong discount without coverage
_STRONG_COVERAGE = 0.75    # near-full coverage: strong discount

# Score discount factors applied to older versions at read time.
_DISCOUNT_STRONG = 0.25
_DISCOUNT_WEAK = 0.50


def token_set(text: str) -> set[str]:
    """Token set for a memory content (CJK bigrams + latin words)."""
    return set(_tokenize(text))


def overlap_signals(
    old_tokens: set[str], new_tokens: set[str]
) -> tuple[float, float]:
    """Return (jaccard, coverage_of_old) between two token sets."""
    if not old_tokens or not new_tokens:
        return 0.0, 0.0
    inter = len(old_tokens & new_tokens)
    union = len(old_tokens | new_tokens)
    return inter / union, inter / len(old_tokens)


def is_revision(jaccard: float, coverage: float) -> bool:
    """Raw similarity signal: do the two contents overlap like versions?

    A pure token-overlap signal. The governance decisions below use
    ``_version_decision``, which additionally requires the newer statement to
    be meaningfully longer — same-length near-identical entries (e.g. a
    numbered list of items) are NOT versions of one fact.
    """
    return jaccard >= _VERSION_JACCARD or coverage >= _COVERAGE_REVISION


def _normalize(content: str) -> str:
    """Canonical form for duplicate detection: whitespace-normalized,
    case-folded. Exact equality here means the entries carry the same
    content — only such entries are deduplicated."""
    return " ".join(content.split()).casefold()


def _version_decision(
    jaccard: float, coverage: float, older_text: str, newer_text: str
) -> bool:
    """Conservative version-of-same-fact test used by governance.

    True when either:
    - the newer statement is meaningfully longer AND fully contains the older
      statement's tokens ("李明住在福州" ⊂ "李明住在福州，后来搬到上海"), or
    - the newer statement is meaningfully longer AND the token overlap is
      strong (near-identical phrasing, e.g. a wording fix).

    Duplicate merging is a separate concern handled by content equality in
    the caller (``_normalize``), never by token similarity: tokenization
    drops single digits, so "第8条" and "第9条" share identical tokens but
    are distinct facts. Same-length near-identical entries are deliberately
    NOT versions for the same reason.
    """
    longer = len(newer_text) >= 1.15 * len(older_text)
    if not longer:
        return False
    if token_set(older_text) <= token_set(newer_text):
        return True
    return jaccard >= _STRONG_JACCARD


def jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity between two contents."""
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _time_key(row: dict[str, Any]) -> tuple[str, int]:
    """Comparable recency key: event time first (message timestamp), fallback
    to write time, then session position as the tie-breaker inside one batch
    (same created_at for messages committed together)."""
    event = row.get("event_at")
    created = row.get("created_at") or ""
    return (event or created, int(row.get("session_position") or 0))


def _is_newer(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when row ``a`` is strictly newer than row ``b``."""
    return _time_key(a) > _time_key(b)


def _audit(
    conn: Any, memory_id: str, action: str, reason: str, at: str
) -> None:
    """Write one auditable governance decision (mirrors Store._log)."""
    if conn is None:
        return
    conn.execute(
        "INSERT INTO audit_log (id, memory_id, action, reason, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex[:12], memory_id, action, reason, at),
    )


def record_possible_updates(
    conn: Any,
    memory_id: str,
    content: str,
    *,
    event_at: str | None,
    session_position: int,
    created_at: str,
    at: str,
    recent: int = 300,
) -> int:
    """Scan recent memories of the same user for same-fact revisions and
    record ``superseded_by`` edges (audited, never destructive).

    Returns the number of edges recorded. Called inside the Add transaction,
    right after the new memory row is inserted.
    """
    new_tokens = token_set(content)
    if not new_tokens:
        return 0
    new_key: dict[str, Any] = {
        "event_at": event_at,
        "session_position": session_position,
        "created_at": created_at,
    }
    rows = conn.execute(
        "SELECT id, content, event_at, session_position, created_at "
        "FROM memories WHERE id != ? AND forgotten_at IS NULL "
        "ORDER BY created_at DESC, session_position DESC LIMIT ?",
        (memory_id, recent),
    ).fetchall()
    recorded = 0
    for row in rows:
        old = dict(row)
        old_tokens = token_set(old["content"])
        if not old_tokens:
            continue
        if not _is_newer(new_key, old):
            continue  # only later statements supersede earlier ones
        if _normalize(old["content"]) == _normalize(content):
            continue  # identical content: dedup is a read-time concern
        jac, coverage = overlap_signals(old_tokens, new_tokens)
        if not _version_decision(jac, coverage, old["content"], content):
            continue
        if jac >= _STRONG_JACCARD or coverage >= _STRONG_COVERAGE:
            conn.execute(
                "INSERT INTO aml_updates (memory_id, superseded_by, "
                "similarity, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(memory_id, superseded_by) DO NOTHING",
                (old["id"], memory_id, round(jac, 4), at),
            )
            _audit(
                conn,
                old["id"],
                "aml_supersede",
                f"superseded_by={memory_id} jaccard={jac:.3f} coverage={coverage:.3f}",
                at,
            )
            recorded += 1
    return recorded


def _sort_key(entry: dict[str, Any]) -> tuple[int, float]:
    """Stable ranking after governance: entries without a score (anchors,
    canon — identity/consensus evidence) keep top priority, then scored
    memories descend by their post-governance score so the current version
    of a fact floats above its discounted older versions."""
    score = entry.get("score")
    if isinstance(score, (int, float)):
        return (1, -float(score))
    return (0, 0.0)


def govern_entries(
    entries: list[dict[str, Any]], at: str, conn: Any = None
) -> list[dict[str, Any]]:
    """Deduplicate and version-suppress the returned evidence window.

    - Identical content (normalized equality): keep the newer entry, drop the
      older one. Token similarity is never used for this, so templated
      entries that differ only in an ordinal stay intact.
    - Same-fact revisions (newer statement meaningfully longer AND either
      fully contains the older statement's tokens or overlaps strongly):
      discount the older entry's score (0.25 for strong overlap, 0.5
      otherwise). The older entry stays in the list, so history questions can
      still find it; it just ranks lower.

    After governance the list is re-ranked: unscored entries (anchors/canon)
    keep priority, then scored entries descend by the new score. Never drops
    more than duplicates and never grows the list. Decisions are audited when
    ``conn`` is given.
    """
    n = len(entries)
    if n < 2:
        if conn is not None:
            # Commit any earlier uncommitted write on this connection
            # (e.g. neighbor-expansion audits from the same read path).
            conn.commit()
        return entries
    drop: set[int] = set()
    discount: dict[int, tuple[float, float]] = {}
    for i in range(n):
        if i in drop:
            continue
        a = entries[i]
        for j in range(i + 1, n):
            if j in drop:
                continue
            b = entries[j]
            older_idx, newer_idx = (j, i) if _is_newer(a, b) else (i, j)
            older_entry, newer_entry = entries[older_idx], entries[newer_idx]
            older_text, newer_text = (
                older_entry["content"],
                newer_entry["content"],
            )
            if _normalize(older_text) == _normalize(newer_text):
                # identical content (only whitespace/case differs): keep one
                drop.add(older_idx)
                _audit(
                    conn,
                    entries[older_idx]["id"],
                    "aml_dedup_merge",
                    f"duplicate_of={entries[newer_idx]['id']}",
                    at,
                )
                continue
            jac, cov = overlap_signals(
                token_set(older_text), token_set(newer_text)
            )
            if not _version_decision(jac, cov, older_text, newer_text):
                continue
            cur = discount.get(older_idx, (0.0, 0.0))
            discount[older_idx] = (max(cur[0], jac), max(cur[1], cov))
    out: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        if idx in drop:
            continue
        item = dict(entry)
        jac, cov = discount.get(idx, (0.0, 0.0))
        if (jac or cov) and isinstance(item.get("score"), (int, float)):
            strong = jac >= _STRONG_JACCARD or cov >= _STRONG_COVERAGE
            factor = _DISCOUNT_STRONG if strong else _DISCOUNT_WEAK
            item["score"] = round(float(item["score"]) * factor, 4)
            _audit(
                conn,
                item["id"],
                "aml_version_discount",
                f"older_version jaccard={jac:.3f} coverage={cov:.3f} "
                f"factor={factor}",
                at,
            )
        out.append(item)
    out.sort(key=_sort_key)
    if conn is not None:
        # Persist read-time governance audits. The project connection uses
        # manual transactions; without an explicit commit every audit INSERT
        # here (and any earlier expansion audit on this connection) would be
        # rolled back when the Store closes.
        conn.commit()
    return out
