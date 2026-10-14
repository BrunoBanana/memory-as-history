"""Deterministic memory governance (v0.2).

Two mechanisms, both deterministic and model-free:

1. Write-time update detection (``record_possible_updates``): after a memory
   is inserted, recent memories of the same user are scanned for same-fact
   revisions — strong token overlap (Jaccard), near-full coverage of the
   older row by the newer statement, or a matching structured-triple key.
   Each detected revision is recorded as a ``superseded_by`` edge in the
   updates table and every decision is written to ``audit_log``. Nothing is
   deleted or rewritten: the history stays intact and auditable, which is
   the project's core philosophy.

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

import re
import uuid
from typing import Any

from ..storage import _tokenize

# Bookkeeping table names. New stores are created with the neutral
# ``memory_*`` schema; stores created by earlier versions use the legacy
# ``aml_*`` names and keep working unchanged (the resolver falls back to the
# legacy name when the neutral one is absent).
_TABLE_ALIASES: dict[str, tuple[str, str]] = {
    "add_log": ("memory_add_log", "aml_add_log"),
    "sessions": ("memory_sessions", "aml_sessions"),
    "updates": ("memory_updates", "aml_updates"),
    "summaries": ("memory_summaries", "aml_summaries"),
    "contradictions": ("memory_contradictions", "aml_contradictions"),
    "time_ctx": ("memory_time_ctx", "aml_time_ctx"),
}


def table_names(conn: Any) -> dict[str, str]:
    """Resolve the active bookkeeping table names for a store.

    Prefers the neutral schema; falls back to the legacy names so
    pre-existing stores (including deployed evaluation stores) stay fully
    readable and writable. One cheap ``sqlite_master`` probe per call is
    negligible next to the query itself.
    """
    existing = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        slot: (neutral if neutral in existing else legacy)
        for slot, (neutral, legacy) in _TABLE_ALIASES.items()
    }

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

# -- v1.5 D1: predicate-group revision detection -------------------------
# "李明住在福州" then "李明搬到上海" share no strong token overlap, so the
# raw Jaccard/coverage gates miss the revision. Grouping predicates by fact
# domain (residence / occupation / preference / acquired / relationship /
# identity) lets a same-domain restatement supersede the older version.
_PREDICATE_GROUPS: dict[str, tuple[str, ...]] = {
    "residence": ("住在", "居住", "搬到", "迁往", "移居", "定居", "回了", "去了", "离开", "搬去", "搬回", "地址", "住址"),
    "occupation": ("工作", "就职", "任职", "上班", "职业", "辞职", "跳槽", "入职"),
    "preference": ("喜欢", "热爱", "讨厌", "偏爱", "最爱", "爱吃", "爱喝", "钟情", "痴迷", "支持", "反对"),
    "acquired": ("养了", "买了", "领养", "收养", "得到", "拥有", "买了辆", "买了只",
                 "养猫", "养狗", "养宠物"),
    "relationship": ("结婚", "离婚", "交往", "分手", "订婚", "恋爱", "成家"),
    "identity": ("改名", "成为", "毕业于", "出生", "年龄", "生日", "国籍", "籍贯",
                 "岁", "多大了", "几岁", "属相"),
    "contact": ("电话", "号码", "手机", "邮箱", "微信", "联系方式", "手机号", "换了号"),
}
# Same-domain restatement with any shared subject token is treated as a
# version candidate even at low raw overlap.
_DOMAIN_JACCARD = 0.15
_DOMAIN_COVERAGE = 0.25


def predicate_group(text: str) -> str | None:
    """Fact domain of a statement, or None when no known predicate fires."""
    if not text:
        return None
    for group, predicates in _PREDICATE_GROUPS.items():
        if any(p in text for p in predicates):
            return group
    return None


def _same_domain_version(old_text: str, new_text: str) -> bool:
    """Same fact domain AND at least one shared subject token: treat the
    newer statement as a version candidate (D1: current state wins)."""
    group = predicate_group(new_text)
    if group is None or predicate_group(old_text) != group:
        return False
    shared = token_set(old_text) & token_set(new_text)
    return bool(shared)


# -- v2.4 D1: structured-triple version detection --------------------------
# The official corpus injects knowledge as JSON triples carrying stable
# identifiers: {"subject": "...", "relation": "...", "object": "...",
# "subject_id": "Q1361", "relation_id": "P30", ...}. Revisions of the same
# fact arrive as an explicit instruction:
#   Corpus: UPDATE: replace the prior value of this subject and relation
#   with the following fact.
#   {"subject": "Hyderabad", "relation": "continent", "object": "Eurasia",
#    "subject_id": "Q1361", "relation_id": "P30", ...}
# The stable ids ARE the same-fact key the governor needs — far stronger than
# any token similarity gate. When both sides carry the same subject_id +
# relation_id (or, without ids, the same subject + relation), the newer
# statement is a version of the older one, regardless of Jaccard/coverage.
_TRIPLE_KEY_RE = re.compile(
    r'"subject_id"\s*:\s*"([^"]+)"\s*,\s*"relation_id"\s*:\s*"([^"]+)"'
)
_TRIPLE_SR_RE = re.compile(
    r'"subject"\s*:\s*"([^"]+)"\s*,\s*"relation"\s*:\s*"([^"]+)"'
)


def _triple_key(text: str) -> tuple[str, str] | None:
    """Stable same-fact key of a structured triple, or None.

    Prefers the official ``subject_id``/``relation_id`` pair (QID/PID); falls
    back to ``subject``/``relation`` when no ids are present. Returns None for
    anything that is not a triple-shaped JSON payload.
    """
    if '"subject"' not in text:
        return None
    m = _TRIPLE_KEY_RE.search(text)
    if m:
        return (m.group(1).lower(), m.group(2).lower())
    m = _TRIPLE_SR_RE.search(text)
    if m:
        return (m.group(1).lower(), m.group(2).lower())
    return None


def _triple_version(old_text: str, new_text: str) -> bool:
    """True when both statements are triples about the SAME subject+relation
    (stable ids take precedence over the human-readable fields)."""
    ok = _triple_key(old_text)
    nk = _triple_key(new_text)
    if ok is None or nk is None:
        return False
    return ok == nk


# -- v1.5 D2: contradiction detection ------------------------------------
# Two statements about the same subject and fact domain whose polarity
# flips (negation appears in exactly one of them) are contradictory, not
# versions: we keep BOTH (the history is preserved), record the pair, and
# at read time surface the partner when either side is retrieved, so the
# answer generator sees the conflict instead of one side only.
_NEGATION_WORDS = ("不", "没", "无", "别", "禁止", "不允许", "反对")
_OPPOSITE_PAIRS: tuple[tuple[str, str], ...] = (
    ("喜欢", "讨厌"),
    ("支持", "反对"),
    ("同意", "拒绝"),
    ("允许", "禁止"),
    ("接受", "拒绝"),
    ("是", "不是"),
)


# -- v2.0 I1: preference revocation --------------------------------------
# "我不喜欢咖啡了" is NOT a contradiction (a stable, continuing conflict
# "她喜欢猫 / 她讨厌猫") — it is a state change: the old preference is
# revoked and the current state wins (D1 version path). Detecting the
# negation on a known fact domain routes revocation away from D2 so the
# old value is discounted instead of being kept alive as a conflict pair.
_REVOCATION_NEG = ("不", "不再", "没", "没有", "从不", "很少")


def _is_revocation(text: str) -> bool:
    """True when the statement negates a known fact domain as a STATE CHANGE
    (a revocation of the earlier state), e.g. "我不喜欢咖啡了". Requires a
    domain predicate AND a change marker (了/再): "她不住在福州" without 了
    is a flat negative assertion (kept as a contradiction, both sides
    preserved), while "她不住在福州了" is a move-out (version). Plain
    negations ("今天不下雨") never count."""
    if not text or predicate_group(text) is None:
        return False
    if "了" not in text and "再" not in text:
        return False
    return any(w in text for w in _REVOCATION_NEG)


def contradiction_signal(old_text: str, new_text: str) -> str | None:
    """Contradiction signal between two statements, or None.

    Fires when both statements are about the same fact domain, share at
    least one subject token, and the polarity flips: either a negation word
    appears in exactly one of them, or each side carries one word of an
    opposite pair. Conservative on purpose — unrelated facts sharing a
    common word must never be flagged.

    v2.0: a revocation on either side ("我不喜欢咖啡了" vs "我喜欢咖啡")
    is a state change, not a conflict — returns None so D1 handles it as a
    version and the current state wins.
    """
    if not old_text or not new_text:
        return None
    if _is_revocation(old_text) or _is_revocation(new_text):
        return None
    group = predicate_group(new_text)
    if group is None or predicate_group(old_text) != group:
        return None
    # Subject/substance overlap at the character level: CJK bigrams make
    # short statements ("她喜欢猫" / "她讨厌猫") share zero bigrams even
    # though they talk about the same subject and object. Requiring two
    # shared characters keeps unrelated same-domain facts apart while
    # catching genuine flips.
    shared_chars = set(old_text) & set(new_text)
    if len(shared_chars) < 2:
        return None
    old_neg = any(w in old_text for w in _NEGATION_WORDS)
    new_neg = any(w in new_text for w in _NEGATION_WORDS)
    if old_neg != new_neg:
        return "negation_flip"
    for a, b in _OPPOSITE_PAIRS:
        if (a in old_text and b in new_text) or (b in old_text and a in new_text):
            return f"opposite:{a}/{b}"
    return None


def record_possible_contradictions(
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
    """Scan recent memories of the same user for contradictory statements
    and record contradiction pairs (audited, never destructive).

    Returns the number of pairs recorded. Called inside the Add
    transaction, after the new memory row is inserted. Pairs are symmetric
    (both directions stored) so read time can find the partner from either
    side with a single query.
    """
    if not content:
        return 0
    t = table_names(conn)
    rows = conn.execute(
        "SELECT id, content, event_at, session_position, created_at "
        "FROM memories WHERE id != ? AND forgotten_at IS NULL "
        "ORDER BY created_at DESC, session_position DESC LIMIT ?",
        (memory_id, recent),
    ).fetchall()
    recorded = 0
    for row in rows:
        old_text = row["content"]
        if not old_text:
            continue
        signal = contradiction_signal(old_text, content)
        if signal is None:
            continue
        conn.execute(
            f"INSERT INTO {t['contradictions']} (memory_id, conflicts_with, "
            "signal, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(memory_id, conflicts_with) DO NOTHING",
            (row["id"], memory_id, signal, at),
        )
        conn.execute(
            f"INSERT INTO {t['contradictions']} (memory_id, conflicts_with, "
            "signal, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(memory_id, conflicts_with) DO NOTHING",
            (memory_id, row["id"], signal, at),
        )
        _audit(
            conn,
            row["id"],
            "contradiction",
            f"conflicts_with={memory_id} signal={signal}",
            at,
        )
        recorded += 1
    return recorded


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
    NOT versions for the same reason. v2.4: structured-triple rewrites do
    NOT go through this gate at all — they are versioned by their stable
    subject_id/relation_id key (``_triple_version``), which is exact and
    immune to template-token inflation.
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
        # v2.4: two structured triples about DIFFERENT subject+relation are
        # never versions of one fact — their template shares nearly every
        # token, so Jaccard/coverage are meaningless between them (a
        # Hyderabad triple and a Beijing triple must not supersede each
        # other just because both carry subject_id/relation_id fields).
        old_triple = _triple_key(old["content"])
        new_triple = _triple_key(content)
        if old_triple is not None and new_triple is not None and old_triple != new_triple:
            continue
        jac, coverage = overlap_signals(old_tokens, new_tokens)
        domain_version = _same_domain_version(
            old["content"], content
        ) or _triple_version(old["content"], content)
        if not domain_version and not _version_decision(
            jac, coverage, old["content"], content
        ):
            continue
        if (jac >= _STRONG_JACCARD or coverage >= _STRONG_COVERAGE
                or domain_version):
            t = table_names(conn)
            conn.execute(
                f"INSERT INTO {t['updates']} (memory_id, superseded_by, "
                "similarity, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(memory_id, superseded_by) DO NOTHING",
                (old["id"], memory_id, round(jac, 4), at),
            )
            _audit(
                conn,
                old["id"],
                "supersede",
                f"superseded_by={memory_id} jaccard={jac:.3f} coverage={coverage:.3f} "
                f"domain={predicate_group(content) or '-'}",
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
    entries: list[dict[str, Any]], at: str, conn: Any = None,
    supersede_discount: float | None = None,
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
    - ``supersede_discount``: when given (a float, typically small), entries
      recorded as superseded by a later statement (write-time updates
      edges) are additionally discounted by that factor. This lets
      current-state questions ("where does X live NOW?") push older versions
      well down while history-anchored queries keep them by passing ``None``.

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
    superseded: set[int] = set()
    if supersede_discount is not None and conn is not None:
        ids = [e["id"] for e in entries]
        if ids:
            try:
                t = table_names(conn)
                rows = conn.execute(
                    f"SELECT DISTINCT memory_id FROM {t['updates']} "
                    f"WHERE memory_id IN ({','.join('?' * len(ids))})",
                    ids,
                ).fetchall()
                superseded = {r[0] for r in rows}
            except Exception:  # noqa: BLE001 - governance must never break search
                pass
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
                    "dedup_merge",
                    f"duplicate_of={entries[newer_idx]['id']}",
                    at,
                )
                continue
            jac, cov = overlap_signals(
                token_set(older_text), token_set(newer_text)
            )
            domain_version = _same_domain_version(older_text, newer_text)
            if not domain_version and not _version_decision(
                jac, cov, older_text, newer_text
            ):
                continue
            cur = discount.get(older_idx, (0.0, 0.0))
            # Domain revisions count as weak evidence unless raw overlap is
            # strong; either way the older version must rank below.
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
                "version_discount",
                f"older_version jaccard={jac:.3f} coverage={cov:.3f} "
                f"factor={factor}",
                at,
            )
        if (supersede_discount is not None and item["id"] in superseded
                and isinstance(item.get("score"), (int, float))):
            item["score"] = round(float(item["score"]) * supersede_discount, 4)
            _audit(
                conn,
                item["id"],
                "supersede_discount",
                f"superseded_current_state factor={supersede_discount}",
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
