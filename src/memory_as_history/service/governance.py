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
# v2.7: domain (predicate-group) version detection only applies between
# short, statement-like texts. Long paragraphs — reassurance, analysis,
# multi-point plans — are not single facts; on real conversation stores
# they restate the same domains constantly and generate false supersede
# edges by the hundreds. Long texts still get revision treatment through
# the Jaccard/coverage path when they genuinely overlap. Structured
# triples (v2.4) are exempt: the stable subject_id/relation_id key is the
# same-fact evidence, regardless of length.
#
# v2.9: the gate is now a CHARACTER count, not a token-set count. The old
# token gate silently failed for English: _tokenize drops stopwords, so a
# 300-character Wikipedia paragraph collapses to <=48 unique content words
# and sails through, generating assistant-vs-assistant false edges on real
# official stores (replay audit: 120/120 false contradiction pairs were
# >48 raw words; 29/45 false version edges likewise). A character count is
# language-neutral and cannot be defeated by stopword filtering.
_DOMAIN_VERSION_MAX_CHARS = 160

# Score discount factors applied to older versions at read time.
_DISCOUNT_STRONG = 0.25
_DISCOUNT_WEAK = 0.50

# -- v1.5 D1: predicate-group revision detection -------------------------
# "李明住在福州" then "李明搬到上海" share no strong token overlap, so the
# raw Jaccard/coverage gates miss the revision. Grouping predicates by fact
# domain (residence / occupation / preference / acquired / relationship /
# identity) lets a same-domain restatement supersede the older version.
# v2.5: English predicates joined the same groups — real-world memory
# corpora are predominantly English, and English non-structured statements
# ("she lived in London" → "she moved to Berlin") had no version detection
# at all before (only the structured-triple path handled them).
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
# English predicates, word-boundary matched so "live" never fires on
# "deliver" and "like" never fires on "alike". Each pattern is a regex.
_EN_PREDICATE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "residence": (
        re.compile(r"\b(?:live|lives|lived|stay|stays|stayed|staying|"
                   r"move|moves|moved|relocat(?:e|ed|ing)|settle|settled|"
                   r"address|zip code)\b", re.I),
    ),
    "occupation": (
        re.compile(r"\b(?:work|works|worked|working|job|occupation|career|"
                   r"employ(?:ed|er|ment)?|hire|hires|hired|join|joined|"
                   r"quit|quits|quitted|resign(?:ed)?|promot(?:ed|ion)?|"
                   r"fire|fired|laid off)\b", re.I),
    ),
    "preference": (
        re.compile(r"\b(?:like|likes|liked|love|loves|loved|hate|hates|"
                   r"hated|prefer|prefers|preferred|enjoy|enjoys|enjoyed|"
                   r"favorite|favourite|fond of|dislike|dislikes|"
                   r"support|supports|supported|oppos(?:e|es|ed)|against|"
                   r"favor|favors|favored|approve|approves|approved)\b", re.I),
    ),
    "acquired": (
        re.compile(r"\b(?:bought?|adopt(?:ed)?|acquire|acquired|purchase|"
                   r"purchased|own|owns|owned|got a new|get a new)\b", re.I),
    ),
    "relationship": (
        re.compile(r"\b(?:marri(?:ed|age)?|divorc(?:ed)?|dating|engaged?|"
                   r"relationship|broke up|breakup|partner|boyfriend|"
                   r"girlfriend|wedding)\b", re.I),
    ),
    "identity": (
        re.compile(r"\b(?:born|birthday|birth place|rename|renamed|became|"
                   r"graduate|graduated|age|years? old|nationality|"
                   r"citizen(?:ship)?)\b", re.I),
    ),
    "contact": (
        re.compile(r"\b(?:phone|mobile|cell(?:phone)?|email|e-mail|contact|"
                   r"whatsapp|telegram|skype)\b", re.I),
    ),
}


def predicate_group(text: str) -> str | None:
    """Fact domain of a statement, or None when no known predicate fires."""
    if not text:
        return None
    for group, predicates in _PREDICATE_GROUPS.items():
        if any(p in text for p in predicates):
            return group
    for group, patterns in _EN_PREDICATE_PATTERNS.items():
        if any(p.search(text) for p in patterns):
            return group
    return None


# English subject-word sharing (v2.5): the shared tokenizer filters English
# stopwords, so "She lived in London" / "She moved to Berlin" share zero
# tokens even though the subject ("she") is identical. For English we share
# the full word set (len>=3, minimal stopword list, pronouns kept) — the
# length gate in ``_version_decision`` still separates genuine restatements
# from same-length different facts.
_EN_VERSION_STOP = frozenset(
    "the a an of to in on for with and or is are was were be been this that "
    "it its as at by from we you i they them his her their our your do does "
    "did not no".split()
)


def _en_content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower())
            if len(w) >= 3 and w not in _EN_VERSION_STOP}


# v2.9: English pronoun subjects are stripped by stopword filtering, so
# "I live in Paris" / "I moved to Berlin" share zero content tokens while
# Chinese keeps the name ("李明住在福州"). The same sentence-initial
# personal pronoun is the subject evidence a version needs (same speaker
# is already guaranteed by the write-time gate).
_EN_PRONOUNS = frozenset("i she he we they you".split())


def _pronoun_subject(text: str) -> str | None:
    words = re.findall(r"[a-z]+", text.lower())
    for w in words[:4]:
        if w in _EN_PRONOUNS:
            return w
    return None


# -- v2.7: change-signal gate for same-domain versions -----------------------
# Same-domain + shared subject alone is NOT a version. Long real
# conversations restate the same domains constantly — a "工作日…" note hits
# occupation, an encouraging "你愿意…" hits preference — so without a change
# signal every later same-domain statement would supersede every earlier
# one (replay of 150 real Chinese messages produced 1603 edges, mostly
# false). A revision is recorded only when either side carries an explicit
# change-denoting predicate (搬到/辞职/改名/买了/moved/quit/divorced…).
# Generic markers ("已经", "开始", "后来", "现在") are deliberately NOT
# signals: they are ubiquitous in narrative text and turned the gate into a
# no-op (replay still produced 1462 edges with them). "李明住在福州" ->
# "李明住在福州，后来搬到上海" fires on 搬到; a flat "她住在杭州" alongside
# "她住在上海" without any change predicate is not a revision.
_ZH_CHANGE_PREDICATES = (
    "搬到", "搬去", "迁往", "移居", "辞职", "跳槽", "离职",
    "分手", "离婚", "结婚", "改名", "买了", "养了", "领养",
    "换了号", "离开", "成为", "出生", "毕业", "入职", "就职",
    "回了", "去了", "走了", "改成", "改为", "换成", "换了",
    "转行", "复职", "上任", "升职", "变成", "变为",
)
_EN_CHANGE_VERB = (
    re.compile(r"\bmove(?:d|s)?\b", re.I),
    re.compile(r"\brelocat(?:e|ed|ing)\b", re.I),
    re.compile(r"\bquit(?:ted)?\b", re.I),
    re.compile(r"\bresign(?:ed)?\b", re.I),
    re.compile(r"\bdivorc(?:e|ed)\b", re.I),
    re.compile(r"\bmarri(?:ed|age)\b", re.I),
    re.compile(r"\bbought?\b", re.I),
    re.compile(r"\badopt(?:ed)?\b", re.I),
    re.compile(r"\bstarted\b", re.I),
    re.compile(r"\bchanged?\b", re.I),
    re.compile(r"\bdecided\b", re.I),
    re.compile(r"\brenamed?\b", re.I),
    re.compile(r"\bswitched\b", re.I),
    re.compile(r"\bjoined\b", re.I),
    re.compile(r"\bhired\b", re.I),
    re.compile(r"\bfired\b", re.I),
)


def _has_change_signal(text: str) -> bool:
    """Explicit state-change signal: a change-denoting predicate (Chinese
    substring), English change verbs / revocation phrases / time markers,
    or a v2.0 revocation ("我不喜欢咖啡了"). Used as the gate before
    recording a same-domain version edge."""
    if not text:
        return False
    if _is_revocation(text):
        return True
    if any(p in text for p in _ZH_CHANGE_PREDICATES):
        return True
    if any(p.search(text) for p in _EN_CHANGE_MARKER):
        return True
    if any(p.search(text) for p in _EN_CHANGE_VERB):
        return True
    if any(p.search(text) for p in _EN_REVOCATION_PHRASE):
        return True
    return False


def _same_domain_version(old_text: str, new_text: str) -> bool:
    """Same fact domain AND at least one shared subject token AND an explicit
    change signal on either side: treat the newer statement as a version
    candidate (D1: current state wins)."""
    group = predicate_group(new_text)
    if group is None or predicate_group(old_text) != group:
        return False
    # v2.7 gate: no change signal, no version edge.
    if not (_has_change_signal(old_text) or _has_change_signal(new_text)):
        # Age statements are the exception: a changed number on the same
        # age subject is a version even without a change word
        # ("我今年25岁" -> "我今年26岁" — age necessarily moves).
        if not (group == "identity" and "岁" in old_text and "岁" in new_text):
            return False
    shared = token_set(old_text) & token_set(new_text)
    if not shared:
        shared = _en_content_words(old_text) & _en_content_words(new_text)
    if not shared:
        # v2.9: same English pronoun subject is shared-subject evidence
        # ("I live in Paris" -> "I moved to Berlin"); a pronoun mismatch
        # (I vs She) means a different subject and stays unshared.
        pa = _pronoun_subject(old_text)
        pb = _pronoun_subject(new_text)
        if pa and pa == pb:
            shared = {pa}
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
# v2.5: English negation and opposite pairs joined the same conservative
# logic (word-boundary so "not" never fires inside "notice").
_NEGATION_WORDS = ("不", "没", "无", "别", "禁止", "不允许", "反对")
# English negation via word-splitting rather than ``\\b`` regexes: Python's
# ``\\b`` does not treat the apostrophe in "doesn't" as a word boundary, so a
# ``\\bn't\\b`` pattern silently misses every contraction. Word-splitting on
# ``[a-z']+`` keeps "doesn't" intact and tests ``endswith("n't")``.
_EN_NEGATION_WORDS = frozenset(("not", "no", "never", "without", "nor", "neither"))


def _has_negation(text: str) -> bool:
    """Chinese substring or English word-boundary negation."""
    if any(w in text for w in _NEGATION_WORDS):
        return True
    for w in re.findall(r"[a-z']+", text.lower()):
        if w in _EN_NEGATION_WORDS:
            return True
        if w.endswith("n't"):
            return True
    return False
_OPPOSITE_PAIRS: tuple[tuple[str, str], ...] = (
    ("喜欢", "讨厌"),
    ("支持", "反对"),
    ("同意", "拒绝"),
    ("允许", "禁止"),
    ("接受", "拒绝"),
    ("是", "不是"),
)
_EN_OPPOSITE_PATTERNS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"\blike[sd]?\b", re.I), re.compile(r"\bhate[sd]?\b", re.I)),
    (re.compile(r"\blove[sd]?\b", re.I), re.compile(r"\bhate[sd]?\b", re.I)),
    (re.compile(r"\bsupport(?:s|ed)?\b", re.I), re.compile(r"\boppos(?:e|es|ed)\b", re.I)),
    (re.compile(r"\bsupport(?:s|ed)?\b", re.I), re.compile(r"\bagainst\b", re.I)),
    (re.compile(r"\bfavor(?:s|ed)?\b", re.I), re.compile(r"\bagainst\b", re.I)),
    (re.compile(r"\bapprove(?:s|d)?\b", re.I), re.compile(r"\breject(?:s|ed)?\b", re.I)),
    (re.compile(r"\ballow(?:s|ed)?\b", re.I), re.compile(r"\bforbid(?:s|den)?\b", re.I)),
    (re.compile(r"\baccept(?:s|ed)?\b", re.I), re.compile(r"\breject(?:s|ed)?\b", re.I)),
    (re.compile(r"\bagree[sd]?\b", re.I), re.compile(r"\bdisagree[sd]?\b", re.I)),
    (re.compile(r"\bmarried\b", re.I), re.compile(r"\bdivorced\b", re.I)),
    (re.compile(r"\bfor\b", re.I), re.compile(r"\bagainst\b", re.I)),
)


def _cross_opposite(a_text: str, b_text: str) -> str | None:
    """Opposite-pair contradiction: one side carries one word of a pair and
    the other side carries its counterpart (Chinese substring or English
    word-boundary), in either direction."""
    for x, y in _OPPOSITE_PAIRS:
        if (x in a_text and y in b_text) or (y in a_text and x in b_text):
            return f"{x}/{y}"
    for px, py in _EN_OPPOSITE_PATTERNS:
        if (px.search(a_text) and py.search(b_text)) or (py.search(a_text) and px.search(b_text)):
            return "en-opposite"
    return None


# -- v2.0 I1: preference revocation --------------------------------------
# "我不喜欢咖啡了" is NOT a contradiction (a stable, continuing conflict
# "她喜欢猫 / 她讨厌猫") — it is a state change: the old preference is
# revoked and the current state wins (D1 version path). Detecting the
# negation on a known fact domain routes revocation away from D2 so the
# old value is discounted instead of being kept alive as a conflict pair.
_REVOCATION_NEG = ("不", "不再", "没", "没有", "从不", "很少")
_EN_CHANGE_MARKER = (
    re.compile(r"\bnow\b", re.I),
    re.compile(r"\banymore\b", re.I),
    re.compile(r"\bany more\b", re.I),
    re.compile(r"\bthese days\b", re.I),
    re.compile(r"\bnowadays\b", re.I),
    re.compile(r"\bsince\b", re.I),
)
_EN_REVOCATION_PHRASE = (
    re.compile(r"\bno longer\b", re.I),
    re.compile(r"\bnot anymore\b", re.I),
    re.compile(r"\bnot any more\b", re.I),
    re.compile(r"\bstopped\b", re.I),
    re.compile(r"\bquit\b", re.I),
    re.compile(r"\bgave up\b", re.I),
)


def _is_revocation(text: str) -> bool:
    """True when the statement negates a known fact domain as a STATE CHANGE
    (a revocation of the earlier state), e.g. "我不喜欢咖啡了" or
    "I don't like coffee anymore". Chinese requires a domain predicate AND a
    change marker (了/再): "她不住在福州" without 了 is a flat negative
    assertion (kept as a contradiction, both sides preserved), while
    "她不住在福州了" is a move-out (version). English requires either an
    explicit revocation phrase ("no longer", "stopped") or a change marker
    ("now", "anymore") combined with negation. Plain negations ("今天不下雨",
    "it is not raining") never count."""
    if not text or predicate_group(text) is None:
        return False
    if ("了" in text or "再" in text) and any(w in text for w in _REVOCATION_NEG):
        return True
    if any(p.search(text) for p in _EN_REVOCATION_PHRASE):
        return True
    if any(p.search(text) for p in _EN_CHANGE_MARKER) and _has_negation(text):
        return True
    return False


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
    old_neg = _has_negation(old_text)
    new_neg = _has_negation(new_text)
    if old_neg != new_neg:
        return "negation_flip"
    return _cross_opposite(old_text, new_text)


def record_possible_contradictions(
    conn: Any,
    memory_id: str,
    content: str,
    *,
    event_at: str | None,
    session_position: int,
    created_at: str,
    at: str,
    source: str | None = None,
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
    new_tokens = token_set(content)
    if not new_tokens:
        return 0
    t = table_names(conn)
    # v2.7: same speaker + short statement-like texts only. Long paragraphs
    # (reassurance, analysis) mix negations with domain words and would flag
    # hundreds of false conflicts; a genuine conflict ("她喜欢猫" vs
    # "她讨厌猫") is a short same-domain statement by the same speaker.
    where = "id != ? AND forgotten_at IS NULL"
    args: list[Any] = [memory_id]
    if source is not None and _has_column(conn, "memories", "source"):
        where += " AND source = ?"
        args.append(source)
    args.append(recent)
    rows = conn.execute(
        f"SELECT id, content, event_at, session_position, created_at "
        f"FROM memories WHERE {where} "
        "ORDER BY created_at DESC, session_position DESC LIMIT ?",
        args,
    ).fetchall()
    recorded = 0
    for row in rows:
        old_text = row["content"]
        if not old_text:
            continue
        old_tokens = token_set(old_text)
        if not old_tokens:
            continue
        if (len(content) > _DOMAIN_VERSION_MAX_CHARS
                or len(old_text) > _DOMAIN_VERSION_MAX_CHARS):
            continue
        # v2.9: structured triples are injected facts, not natural-language
        # statements — polarity signals are meaningless for JSON payloads
        # and generated false negation_flip pairs on real official stores.
        if _triple_key(old_text) is not None or _triple_key(content) is not None:
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


def _has_column(conn: Any, table: str, column: str) -> bool:
    """True when the table actually carries the column. Older stores may
    predate a column that newer schemas declare; governance must never
    crash an Add on a legacy store (project principle: old stores stay
    readable and writable)."""
    try:
        return any(
            r[1] == column
            for r in conn.execute(f"PRAGMA table_info({table})")
        )
    except Exception:  # noqa: BLE001 - a probe failure means "no column"
        return False


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
    source: str | None = None,
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
    # v2.7: only statements by the SAME speaker form versions of each other.
    # Long conversations interleave user facts and assistant restatements /
    # reassurance; without this filter every later same-domain assistant
    # paragraph would supersede the user's own statements (and vice versa) —
    # replay of 150 real Chinese messages dropped from 1603 to 381 edges by
    # the change-signal gate alone, and the remaining noise was almost all
    # cross-speaker. A fact is updated by the person who states it.
    # v2.7.1: the gate is applied per candidate (not in SQL) so structured
    # triples stay exempt — their stable subject_id/relation_id key is exact
    # same-fact evidence, and real corpora inject the UPDATE instruction
    # under a different role than the stored triple (1614-store scan:
    # triples are ~all user, yet 85 update instructions arrive as assistant).
    # A missing source column on a legacy store simply disables the gate.
    has_source_col = _has_column(conn, "memories", "source")
    where = "id != ? AND forgotten_at IS NULL"
    args: list[Any] = [memory_id]
    args.append(recent)
    select_cols = (
        "id, content, source, event_at, session_position, created_at"
        if has_source_col
        else "id, content, event_at, session_position, created_at"
    )
    rows = conn.execute(
        f"SELECT {select_cols} FROM memories WHERE {where} "
        "ORDER BY created_at DESC, session_position DESC LIMIT ?",
        args,
    ).fetchall()
    recorded = 0
    for row in rows:
        old = dict(row)
        if has_source_col and source is not None and old.get("source") is not None:
            if old["source"] != source and not _triple_version(
                old["content"], content
            ):
                continue
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
        # v2.7: predicate-group domain versioning only between short
        # statement-like texts; long paragraphs (reassurance, analysis,
        # plans) are not single facts and would otherwise supersede
        # everything in their domain. Structured triples stay exempt.
        # v2.9: the gate is a character count — the token-set gate was
        # defeated by English stopword filtering (see _DOMAIN_VERSION_MAX_CHARS).
        domain_version = False
        if (len(content) <= _DOMAIN_VERSION_MAX_CHARS
                and len(old["content"]) <= _DOMAIN_VERSION_MAX_CHARS):
            domain_version = _same_domain_version(
                old["content"], content
            )
        domain_version = domain_version or _triple_version(
            old["content"], content
        )
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
            # v2.9: same-speaker gate at read time. Write time (v2.7) only
            # lets the person who states a fact revise it; the read-time
            # discount must be consistent — a cross-speaker restatement in
            # the window must not silently depress the speaker's own fact.
            src_old = entries[older_idx].get("_source")
            src_new = entries[newer_idx].get("_source")
            if src_old is not None and src_new is not None and src_old != src_new:
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
