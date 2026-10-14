"""Per-user memory service implementing a standardized Add/Search
semantics (the same write/read contract used by evaluation harnesses).

Isolation by construction: each ``user_id`` owns one SQLite file, so
cross-user retrieval is impossible — the Search scope and the write target
are the same physical database.

Write path: one transaction per Add request. The idempotency marker
(add-log table), the memory rows and the per-session position counter are
committed together, so a replay with the same ``request_id`` either finds a
completed marker (and is echoed without re-writing) or re-runs from a clean
rollback. There is no partial-write state to recover.

Read path: reuses the project ``Store.recall`` (BM25, anchors/canon
priority) and optionally ``Store.search`` (hybrid) on the same user
database, flattened to the standard ``data`` array. The memories table is
the project schema; the service only adds a few bookkeeping tables of its
own (neutral ``memory_*`` names; legacy ``aml_*`` stores are read
transparently, see ``governance.table_names``).
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
    predicate_group,
    record_possible_contradictions,
    record_possible_updates,
    table_names,
)
from .temporal import detect_temporal_clues, parse_iso_ts, time_weight

# Default storage root. The ``memory`` sub-directory is the current name;
# a legacy ``aml`` sub-directory is picked up when present so older local
# stores keep working without a migration.
_LEGACY_DATA_DIR = Path.home() / ".memory-as-history" / "aml"
_MEMORY_DATA_DIR = Path.home() / ".memory-as-history" / "memory"
DEFAULT_DATA_DIR = (
    _LEGACY_DATA_DIR
    if _LEGACY_DATA_DIR.exists() and not _MEMORY_DATA_DIR.exists()
    else _MEMORY_DATA_DIR
)

# Service-owned bookkeeping tables (neutral names; legacy stores use the
# ``aml_`` names, which ``governance.table_names`` resolves transparently).
MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_add_log (
    request_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_sessions (
    session_id    TEXT PRIMARY KEY,
    next_position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memory_updates (
    memory_id     TEXT NOT NULL,
    superseded_by TEXT NOT NULL,
    similarity    REAL NOT NULL,
    at            TEXT NOT NULL,
    PRIMARY KEY (memory_id, superseded_by)
);
CREATE TABLE IF NOT EXISTS memory_summaries (
    session_id TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_contradictions (
    memory_id     TEXT NOT NULL,
    conflicts_with TEXT NOT NULL,
    signal        TEXT NOT NULL,
    at            TEXT NOT NULL,
    PRIMARY KEY (memory_id, conflicts_with)
);
CREATE TABLE IF NOT EXISTS memory_time_ctx (
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


# -- v2.1 G2: rule-QA boost ----------------------------------------------
# "她可以吃辣吗 / can she eat spicy food" asks about a rule: the stored
# rule statement ("她不能吃辣 / she never eats spicy") is the answer, and
# it must rank above merely-similar memories. Rule-question patterns get a
# lift on memories carrying rule indicators.
_RULE_QA_PATTERN = re.compile(
    r"可以.{0,6}吗|能不能|可不可以|允许.{0,6}吗|是否.{0,6}(?:可以|允许)|"
    r"有什么讲究|有什么忌讳|需要注意什么|"
    r"\b(can she|could she|is she allowed|am i allowed|is it ok|"
    r"is it okay|does she always|does she never|is she permitted)\b"
)
_RULE_INDICATORS = (
    "不能", "不可以", "不行", "不许", "不准", "禁止", "不允许",
    "never", "always", "don't", "do not", "must not", "forbidden",
    "prohibited", "not allowed", "忌", "讲究", "注意", "小心",
)


def _rule_qa_boost(query: str, entries: list[dict]) -> list[dict]:
    """Lift rule-carrying memories when the question is a rule question.
    Boosts the *minimum* of rule candidates to 0.9 so the answer generator
    sees the rule first; non-rule memories keep their own scores."""
    if not _RULE_QA_PATTERN.search(query.lower()):
        return entries
    for item in entries:
        if "score" not in item:
            continue
        content = item["content"].lower()
        if any(ind in content for ind in _RULE_INDICATORS):
            item["score"] = max(item["score"], 0.9)
    return entries


# -- v1.8 A1: synonym query expansion ------------------------------------
# A single-fact question ("她的手机号是多少") often uses words the memory
# does not ("我的电话号码是…"). Expanding the retrieval input with a small
# domain dictionary lets BM25 match the stored phrasing without touching
# ranking semantics: expansions are appended as ordinary tokens, so the
# original query words keep their natural weight. Triggers (summary/rule/
# temporal) still run on the original query, never on the expansion.
_QUERY_SYNONYMS_ZH: dict[str, tuple[str, ...]] = {
    "电话": ("手机", "联系方式", "号码", "手机号", "电话号码"),
    "手机": ("电话", "联系方式", "号码", "手机号"),
    "手机号": ("电话", "手机", "号码", "联系方式"),
    "联系方式": ("电话", "手机", "号码", "微信", "邮箱"),
    "号码": ("电话", "手机", "联系方式"),
    "微信": ("联系方式", "号码", "微信"),
    "邮箱": ("联系方式", "邮件", "电子邮箱"),
    "邮件": ("邮箱", "电子邮件"),
    "名字": ("姓名", "叫什么", "称呼"),
    "姓名": ("名字", "叫什么", "称呼"),
    "住": ("住在", "居住", "家", "住址"),
    "住在": ("居住", "住", "家住"),
    "家": ("住在", "居住", "住址", "家在"),
    "喜欢": ("爱", "偏爱", "最爱", "中意"),
    "爱": ("喜欢", "偏爱", "最爱"),
    "讨厌": ("不喜欢", "反感"),
    "工作": ("职业", "上班", "职位", "干什么"),
    "职业": ("工作", "职位", "上班"),
    "宠物": ("养了", "养", "猫", "狗"),
    "猫": ("猫咪", "小猫"),
    "狗": ("狗狗", "小狗"),
}
_QUERY_SYNONYMS_EN: dict[str, tuple[str, ...]] = {
    "phone": ("number", "mobile", "contact", "tel"),
    "number": ("phone", "mobile", "contact"),
    "mobile": ("phone", "number"),
    "email": ("mail", "contact", "e-mail"),
    "name": ("called", "call me"),
    "live": ("lived", "reside", "stay", "residing"),
    "lives": ("lived", "reside", "stays"),
    "like": ("love", "prefer", "favorite", "enjoy"),
    "likes": ("loves", "prefers", "enjoys"),
    "job": ("work", "occupation", "career", "position"),
    "work": ("job", "occupation", "career"),
    "pet": ("cat", "dog", "animal"),
    "cat": ("kitten", "kitty"),
    "dog": ("puppy"),
}


def _expand_query(query: str) -> str:
    """Retrieval-input expansion: append synonyms of words the query uses.
    Returns the original query unchanged when nothing expands."""
    if not query:
        return query
    extra: list[str] = []
    for word, syns in _QUERY_SYNONYMS_ZH.items():
        if word in query:
            for s in syns:
                if s not in query and s not in extra:
                    extra.append(s)
    lowered = query.lower()
    for word, syns in _QUERY_SYNONYMS_EN.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            for s in syns:
                if s.lower() not in lowered and s not in extra:
                    extra.append(s)
    if not extra:
        return query
    return query + " " + " ".join(extra)


# -- v1.7 B2: causal questions widen the neighbor window -----------------
# "为什么/怎么/后来/how did/why" need the chain of steps, not one turn;
# a wider expansion radius pulls the path that led to the outcome.
_CAUSAL_QUERY_PATTERN = re.compile(
    r"为什么|什么原因|原因|因为|所以|怎么|如何|导致|结果|变成|后来|"
    r"起因|经过|造成|影响|于是|因此|随之|演变|前因后果|来由|缘由|缘故|后果|来龙去脉|"
    r"what happened|why|because|how did|how come|what led to|what caused|"
    r"what triggered|result of|result in|then what|what happened next|"
    r"consequence|effect of|stem from|arise from|what came of|why did"
)


def _is_causal_query(query: str) -> bool:
    return bool(query) and _CAUSAL_QUERY_PATTERN.search(query) is not None


# -- v1.9 C2: order questions --------------------------------------------
# "先后/顺序/按时间/timeline" ask for the story in the order it happened.
# These reorder the evidence by message time so the answer generator does
# not narrate events out of sequence.
_ORDER_QUERY_PATTERN = re.compile(
    r"先后|顺序|按时间|一步步|过程|时间线|按顺序|"
    r"timeline|in order|sequence|step by step|chronological|"
    r"what happened first|before that|after that|then what|"
    r"先.{0,4}后"
)


# -- v2.3 I1: cross-session preference aggregation -----------------------
# "她喜欢什么 / what does she like" must aggregate preferences expressed
# across sessions in different words ("我喜欢猫" s1, "我爱吃火锅" s2).
# A generic preference question appends the user's preference-domain
# memories (mid score) so the answer generator sees all of them; a
# specific one ("她喜欢猫吗") still ranks its BM25 hit first.
_PREFERENCE_QA_PATTERN = re.compile(
    r"喜欢什么|爱吃什么|爱喝什么|最爱|偏好|兴趣|爱好|喜欢哪|喜欢些|"
    r"\b(prefer|prefers|favorite|favourite|hobbies|hobby|"
    r"what does (?:she|he|the user) like)\b"
)


def _preference_qa_append(
    query: str, entries: list[dict], conn: Any
) -> list[dict]:
    """Append unseen preference-domain memories for a generic preference
    question. Filtered by the governance domain so 爱/喜欢 hits like
    "爱人" or "可爱" never leak in. Scored 0.6: below a specific BM25 hit,
    above nothing — they answer the open-ended question."""
    if not _PREFERENCE_QA_PATTERN.search(query.lower()) or conn is None:
        return entries
    seen = {e["id"] for e in entries}
    try:
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE forgotten_at IS NULL "
            "ORDER BY event_at DESC, created_at DESC LIMIT 200"
        ).fetchall()
    except sqlite3.Error:
        return entries
    for r in rows:
        if r["id"] in seen:
            continue
        if predicate_group(r["content"]) != "preference":
            continue
        entries.append(
            {
                "id": r["id"],
                "content": r["content"],
                "score": 0.6,
                "created_at": r["created_at"],
            }
        )
        seen.add(r["id"])
    return entries


def _is_order_query(query: str) -> bool:
    return bool(query) and _ORDER_QUERY_PATTERN.search(query) is not None


# -- v2.2 C1: relative-time anchoring ------------------------------------
# "最近/上周/昨天/recently/last week" ask about the newest slice of the
# conversation. Memories that literally say "最近" already match by BM25;
# the ones that only carry a recent timestamp ("我搬到上海了") need a
# recency lift so the current state answers instead of an older memory.
_RECENT_TIME_PATTERN = re.compile(
    r"最近|近期|上周|这周|本周|昨天|前天|刚才|刚刚|今天|这两天|这几(?:天|周|个月)|"
    r"\b(recent(?:ly)?|yesterday|last (?:week|month|night|time|weekend)|"
    r"just now|earlier (?:today|this week)|these days)\b"
)


def _recent_time_boost(
    query: str, entries: list[dict], times: dict[str, float | None]
) -> list[dict]:
    """Lift the newest third of scored evidence when the question asks about
    recent time. Timestamps come from event_at (message time), which spans
    real conversation time even inside one batch."""
    if not entries or not _RECENT_TIME_PATTERN.search(query.lower()):
        return entries
    scored = [(i, e) for i, e in enumerate(entries) if "score" in e]
    if not scored:
        return entries
    ordered = sorted(
        scored, key=lambda p: -(times.get(p[1]["id"]) or 0.0)
    )
    n = max(1, int(len(ordered) * 0.3))
    top_ids = {e["id"] for _, e in ordered[:n]}
    for item in entries:
        if "score" in item and item["id"] in top_ids:
            item["score"] = min(item["score"] + 0.15, 1.0)
    return entries


# -- v1.6 C1: content years ----------------------------------------------
# Years named inside memory content ("2021年我住在福州", "in 2020 we moved",
# "during 2019"). Stored in the time-context table at write time; a
# question anchored to a year boosts the memories that describe that year.
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
# the two write paths aligned is enforced by tests/test_service_adapter.py.
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
    # Project schema + service bookkeeping share one creation script, then
    # the same additive migrations Store applies. executescript commits any
    # open transaction first; the script itself carries the BEGIN IMMEDIATE
    # so creation and upgrades share the lock.
    conn.executescript(
        "BEGIN IMMEDIATE;\n" + SCHEMA + KNOWLEDGE_SCHEMA + MEMORY_SCHEMA
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
            "turn_expand",
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


def _point(text: str, limit: int = 160) -> str:
    """One summary point from a message: first non-empty line, truncated.
    v1.7: 96 -> 160 chars so a review answer has the substance (who/what/
    where), not just the sentence head."""
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
    t = table_names(conn)
    row = conn.execute(
        f"SELECT summary FROM {t['summaries']} WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    old = row["summary"] if row is not None else None
    new = _update_summary(old, point)
    if new == old and row is not None:
        return
    conn.execute(
        f"INSERT OR REPLACE INTO {t['summaries']} (session_id, summary, updated_at) "
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
    """Memory write/read service for one data directory.

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
        # inference). The service runs on a 2 GB host; without this,
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
            f"[mem-add] rid={req['request_id'][:24]} "
            f"uid={req['user_id'][:24]} sid={req['session_id'][:24]} "
            f"msgs={len(req['messages'])} at={_now()}",
            flush=True,
        )
        conn = self._open_connection(req["user_id"])
        try:
            conn.execute("BEGIN IMMEDIATE")
            t = table_names(conn)
            row = conn.execute(
                f"SELECT status FROM {t['add_log']} WHERE request_id = ?",
                (req["request_id"],),
            ).fetchone()
            if row is not None and row["status"] == "done":
                conn.commit()
                return add_ok(
                    req["request_id"], req["user_id"], req["session_id"]
                )
            at = _now()
            conn.execute(
                f"INSERT OR REPLACE INTO {t['add_log']} "
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
                            f"INSERT OR IGNORE INTO {t['time_ctx']} "
                            "(memory_id, year, at) VALUES (?, ?, ?)",
                            (mid, year, at),
                        )
            conn.execute(
                f"INSERT INTO {t['sessions']} (session_id, next_position) "
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
        t = table_names(conn)
        row = conn.execute(
            f"SELECT next_position FROM {t['sessions']} WHERE session_id = ?",
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
            f"[mem-search] uid={req['user_id'][:24]} "
            f"q={req['query'][:200]} top_k={req.get('top_k')} at={_now()}",
            flush=True,
        )
        store = self._user_store(req["user_id"])
        data: list[dict] = []
        # A1: expand the retrieval input with synonyms so "手机号" matches
        # stored "电话" and "家" matches "住在". Triggers (summary / rule /
        # temporal) still use the original query below.
        retrieval_query = _expand_query(req["query"])
        try:
            # The expensive stage (embedding inference) runs inside the
            # concurrency gate. Queued requests wait here; memory stays
            # bounded on the 2 GB host instead of OOM-killing the worker.
            with self._search_gate:
                if self.search_mode == "lexical":
                    result = store.recall(retrieval_query, limit=req["top_k"])
                else:
                    # candidate_cap: BM25 pre-filters before semantic rerank.
                    # Official Full stores run into thousands of entries;
                    # embedding all of them blows the 2 GB host's request
                    # budget (SEARCH_TIMEOUT, observed 2026-10-05). 300 keeps
                    # top_k=100 well covered while cutting inference ~5-10x.
                    result = store.search(
                        retrieval_query,
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
                        store._conn,
                        seeds,
                        retrieval_query,
                        req["top_k"],
                        radius=5 if _is_causal_query(req["query"]) else None,
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
                                        "temporal_hit",
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
            # G2: rule questions ("可以…吗/can she") lift rule statements
            # above merely-similar memories.
            data = _rule_qa_boost(req["query"], data)
            # I1: generic preference questions ("她喜欢什么") aggregate the
            # preference domain across sessions.
            data = _preference_qa_append(req["query"], data, store._conn)
            # C1: relative-time questions ("最近/上周/yesterday") answer
            # with the newest slice — which may share no word with the
            # question. Append the newest memories (mid score), then lift the
            # newest third so the current state wins.
            if _RECENT_TIME_PATTERN.search(req["query"].lower()):
                seen = {e["id"] for e in data}
                for m in self._recent_memories(store, req["top_k"]):
                    if m["id"] in seen:
                        continue
                    data.append(
                        {
                            "id": m["id"],
                            "content": m["content"],
                            "score": 0.7,
                            "created_at": m["created_at"],
                        }
                    )
                    seen.add(m["id"])
            data = _recent_time_boost(
                req["query"], data, self._event_times(store, data)
            )
            # D2: when either side of a recorded contradiction is in the
            # evidence, pull in the other side so the answer generator sees
            # the conflict in full instead of one-sided evidence. Appended
            # after the ranked evidence (never ahead of it); Top K bounds
            # the total.
            partners = self._contradiction_partners(store, data)
            if partners:
                data = data + partners
            # C2: order questions ("先后/顺序/timeline") reorder the evidence
            # by message time ascending, so the story is narrated as it
            # happened. Unscored anchors/canon keep priority.
            if _is_order_query(req["query"]):
                data = self._order_by_time(store, data)
        finally:
            store.close()
        # Boosted scores (rule-QA / recency) must re-rank the evidence.
        # Unscored anchors/canon stay ahead of scored entries; only order
        # questions keep the chronological arrangement from above.
        if not _is_order_query(req["query"]):
            data.sort(
                key=lambda e: (
                    0 if "score" not in e else 1,
                    -(e.get("score") or 0.0),
                )
            )
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
        t = table_names(conn)
        rows = conn.execute(
            f"SELECT session_id, summary FROM {t['summaries']}"
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
                f"INSERT OR REPLACE INTO {t['summaries']} (session_id, summary, updated_at) "
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
            t = table_names(conn)
            q = (
                f"SELECT memory_id, conflicts_with, signal FROM {t['contradictions']} "
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
                            "contradiction_partner",
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
            t = table_names(conn)
            rows = conn.execute(
                f"SELECT memory_id FROM {t['time_ctx']} "
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
                            "time_ctx_boost",
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

    def _event_times(self, store: Store, entries: list[dict]) -> dict[str, float | None]:
        """id -> event_at epoch for the given entries (message timestamps
        span real conversation time, unlike created_at which is shared by a
        batch). Falls back to empty on any DB error."""
        conn = store._conn
        times: dict[str, float | None] = {}
        if conn is not None and entries:
            ids = [e["id"] for e in entries]
            try:
                rows = conn.execute(
                    "SELECT id, event_at FROM memories "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                ).fetchall()
                for r in rows:
                    times[r["id"]] = parse_iso_ts(r["event_at"])
            except sqlite3.Error:
                pass
        return times

    def _recent_memories(self, store: Store, limit: int) -> list[dict]:
        """Newest memories of the user by message time (C1: relative-time
        questions). These may share no word with the question ("我搬到上海了"
        vs "她最近住在哪里"), yet they ARE the answer."""
        conn = store._conn
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT id, content, created_at FROM memories "
                "WHERE forgotten_at IS NULL "
                "ORDER BY event_at DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def _order_by_time(
        self, store: Store, entries: list[dict]
    ) -> list[dict]:
        """Reorder evidence by message time ascending (C2: sequence
        questions). event_at (the message's own timestamp, spans real
        conversation time) wins over created_at; unscored anchors/canon
        keep their priority above the sequence. Audited when entries move."""
        if len(entries) < 2:
            return entries
        times = self._event_times(store, entries)
        ordered = sorted(
            entries,
            key=lambda e: (
                0 if "score" not in e else 1,
                times.get(e["id"])
                or parse_iso_ts(e.get("created_at"))
                or 0.0,
            ),
        )
        if ordered != entries and conn is not None:
            try:
                conn.execute(
                    "INSERT INTO audit_log (id, memory_id, action, reason, at) "
                    "VALUES (?, NULL, ?, ?, ?)",
                    (
                        uuid.uuid4().hex[:12],
                        "order_rerank",
                        f"chronological n={len(entries)}",
                        _now(),
                    ),
                )
                conn.commit()
            except sqlite3.Error:
                pass
        return ordered

    def _expand_neighbors(
        self,
        conn: sqlite3.Connection,
        seed_rows: list[dict],
        query: str,
        top_k: int,
        radius: int | None = None,
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
        budget. Every expansion decision is audited (``turn_expand``).
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
        window = radius if radius is not None else int(
            os.environ.get("MEMORY_EXPAND_RADIUS")
            or os.environ.get("AML_EXPAND_RADIUS", "3")
        )
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
                if radius is not None:
                    # Causal questions widen the window AND relax the token
                    # gate: the middle step of a chain ("价格谈不拢") often
                    # shares no token with the outcome ("合作取消了") or the
                    # question ("为什么合作取消"), yet it is exactly the
                    # evidence the answer needs. Every in-window neighbor is
                    # admitted at the same low evidence weight.
                    hits = 1 if tokens else 0
                else:
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
