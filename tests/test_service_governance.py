"""Tests for the governance layer (v0.2).

Deterministic, model-free memory governance:

- write-time update detection records ``superseded_by`` edges in
  ``memory_updates`` (audited, non-destructive);
- read-time suppression deduplicates near-exact evidence and discounts
  older versions of the same fact, while keeping them retrievable so
  history-style questions still work.

The contract must remain untouched: every Search response entry is still
``{"id", "content", "score"?, "created_at"?}`` and governance never removes
an entry that would otherwise be returned (except exact duplicates).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from memory_as_history.service import MemoryService
from memory_as_history.service.governance import (
    _version_decision,
    govern_entries,
    is_revision,
    jaccard,
    overlap_signals,
    record_possible_updates,
    token_set,
)


def _service() -> tuple[MemoryService, Path]:
    data_dir = Path(tempfile.mkdtemp(prefix="mem-gov-"))
    return MemoryService(data_dir=data_dir), data_dir


def _add(service, request_id, user_id, session_id, messages):
    return service.add(
        {
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "messages": messages,
        }
    )


def _search(service, query, user_id, top_k=100, **extra):
    body = {"query": query, "user_id": user_id, "top_k": top_k, **extra}
    return service.search(body)


def _conn(service, user_id="u1") -> sqlite3.Connection:
    return service._open_connection(user_id)


# -- similarity signals ---------------------------------------------------


def test_coverage_detects_extension_that_jaccard_misses():
    old_tokens = token_set("李明住在福州")
    new_tokens = token_set("李明住在福州，后来搬到上海")
    jac, cov = overlap_signals(old_tokens, new_tokens)
    # raw Jaccard is only ~0.5 for CJK bigrams even for a clear extension
    assert jac < 0.6, f"Jaccard should stay below the strong threshold: {jac:.3f}"
    assert cov >= 0.75
    assert is_revision(jac, cov)


def test_disjoint_facts_are_not_revisions():
    assert not is_revision(*overlap_signals(
        token_set("李明住在福州"), token_set("王五喜欢咖啡")
    ))


def test_near_exact_duplicate_is_a_revision():
    assert is_revision(*overlap_signals(
        token_set("用户 A 的宠物是猫"), token_set("用户 A 的宠物是猫")
    ))


def test_same_length_near_identical_entries_are_not_versions():
    # Numbered/templated memories (same length, only the ordinal differs)
    # are distinct facts, not versions of one fact. Without this guard a
    # whole list of similar items would collapse into a single entry.
    old_text, new_text = "记忆内容第8条，包含关键词芒果", "记忆内容第9条，包含关键词芒果"
    jac, cov = overlap_signals(token_set(old_text), token_set(new_text))
    assert is_revision(jac, cov)  # raw signal fires...
    assert not _version_decision(jac, cov, old_text, new_text)  # ...but length gate blocks it
    out = govern_entries(
        [
            {"id": "a", "content": old_text, "score": 0.5,
             "created_at": "2026-01-01T00:00:00Z"},
            {"id": "b", "content": new_text, "score": 0.5,
             "created_at": "2026-02-01T00:00:00Z"},
        ],
        "2026-03-01T00:00:00Z",
    )
    assert len(out) == 2
    assert {e["id"] for e in out} == {"a", "b"}


# -- write-time update detection ------------------------------------------


def test_update_detection_records_supersede_edge_and_audit():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    _add(
        service, "r2", "u1", "s1",
        [{"role": "user", "timestamp": 2000, "content": "李明住在福州，后来搬到上海"}],
    )
    conn = _conn(service)
    try:
        edges = conn.execute(
            "SELECT memory_id, superseded_by FROM memory_updates"
        ).fetchall()
        audits = conn.execute(
            "SELECT action FROM audit_log WHERE action = 'supersede'"
        ).fetchall()
    finally:
        conn.close()
    assert len(edges) == 1
    assert len(audits) == 1
    assert edges[0]["superseded_by"] != edges[0]["memory_id"]


def test_old_statement_cannot_supersede_newer_one():
    service, _ = _service()
    # the "revision" arrives FIRST, the older fact LATER in time: no edge.
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 2000, "content": "李明住在福州，后来搬到上海"}],
    )
    _add(
        service, "r2", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    conn = _conn(service)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM memory_updates").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0


def test_exact_duplicate_is_not_recorded_as_update():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "完全一样的内容"}])
    _add(service, "r2", "u1", "s1", [{"role": "user", "content": "完全一样的内容"}])
    conn = _conn(service)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM memory_updates").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0  # dedup is a read-time concern


def test_idempotent_replay_does_not_duplicate_updates():
    service, _ = _service()
    msg_old = [{"role": "user", "timestamp": 1000, "content": "事件：买了苹果"}]
    msg_new = [{"role": "user", "timestamp": 2000, "content": "事件：买了苹果，后来又退了"}]
    _add(service, "r1", "u1", "s1", msg_old)
    _add(service, "r2", "u1", "s1", msg_new)
    _add(service, "r2", "u1", "s1", msg_new)  # replay
    conn = _conn(service)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM memory_updates").fetchone()["n"]
    finally:
        conn.close()
    assert n == 1


# -- read-time version suppression ----------------------------------------


def test_older_version_is_discounted_but_still_retrievable():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    _add(
        service, "r2", "u1", "s1",
        [{"role": "user", "timestamp": 2000, "content": "李明住在福州，后来搬到上海"}],
    )
    result = _search(service, "李明住在福州", "u1", top_k=100)
    contents = [item["content"] for item in result["data"]]
    assert len(result["data"]) == 2, "older version must stay retrievable"
    assert any("搬到上海" in c for c in contents)
    assert any("住在福州" in c and "搬到上海" not in c for c in contents)
    # the newer statement must rank first (old one was discounted)
    assert "搬到上海" in result["data"][0]["content"]
    new_score = result["data"][0]["score"]
    old_score = result["data"][1]["score"]
    assert new_score > old_score
    assert old_score > 0


def test_near_exact_duplicate_is_deduplicated():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "用户 A 的宠物是猫"}])
    _add(service, "r2", "u1", "s1", [{"role": "user", "content": "用户 A 的宠物是猫"}])
    result = _search(service, "宠物", "u1", top_k=100)
    assert len(result["data"]) == 1


def test_unrelated_memories_are_not_touched():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "我在杭州做后端开发"}])
    _add(service, "r2", "u1", "s1", [{"role": "user", "content": "她喜欢去健身房"}])
    result = _search(service, "杭州 后端", "u1", top_k=100)
    assert len(result["data"]) == 1
    assert result["data"][0]["content"] == "我在杭州做后端开发"


def test_governance_keeps_response_contract_clean():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "李明住在福州"}])
    _add(
        service, "r2", "u1", "s1",
        [{"role": "user", "content": "李明住在福州，后来搬到上海"}],
    )
    result = _search(service, "李明", "u1", top_k=100)
    for item in result["data"]:
        assert set(item) <= {"id", "content", "score", "created_at"}


def test_governance_entries_directly():
    entries = [
        {"id": "old", "content": "李明住在福州", "score": 0.8, "created_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "content": "李明住在福州，后来搬到上海", "score": 0.6, "created_at": "2026-02-01T00:00:00Z"},
    ]
    out = govern_entries(entries, "2026-03-01T00:00:00Z")
    assert [e["id"] for e in out] == ["new", "old"]
    assert out[1]["score"] == pytest.approx(0.8 * 0.25, abs=1e-3)


def test_govern_entries_supersede_discount_current_state():
    entries = [
        {"id": "old", "content": "李明住在福州", "score": 0.8, "created_at": "2026-01-01T00:00:00Z"},
        {"id": "new", "content": "李明住在福州，后来搬到上海", "score": 0.6, "created_at": "2026-02-01T00:00:00Z"},
    ]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE memory_updates (memory_id TEXT NOT NULL, "
        "superseded_by TEXT NOT NULL, similarity REAL, at TEXT);"
        "CREATE TABLE audit_log (id TEXT, memory_id TEXT, action TEXT, "
        "reason TEXT, at TEXT);"
    )
    conn.execute(
        "INSERT INTO memory_updates VALUES ('old','new',0.8,'2026-02-01T00:00:00Z')"
    )
    conn.commit()
    # current-state question (no temporal clue): extra supersede discount
    out = govern_entries(entries, "2026-03-01T00:00:00Z", conn=conn, supersede_discount=0.1)
    assert [e["id"] for e in out] == ["new", "old"]
    assert out[1]["score"] == pytest.approx(0.8 * 0.25 * 0.1, abs=1e-4)
    # history-anchored question: no extra discount, old version stays strong
    out2 = govern_entries(entries, "2026-03-01T00:00:00Z", conn=conn, supersede_discount=None)
    assert out2[1]["score"] == pytest.approx(0.8 * 0.25, abs=1e-3)


def test_record_possible_updates_returns_edge_count():
    import uuid as _uuid

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT, event_at TEXT,
            session_position INTEGER, created_at TEXT, forgotten_at TEXT
        );
        CREATE TABLE memory_updates (
            memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL,
            similarity REAL NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, superseded_by)
        );
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY, memory_id TEXT, action TEXT,
            reason TEXT, at TEXT
        );
        """
    )
    old_id = _uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, NULL)",
        (old_id, "李明住在福州", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    count = record_possible_updates(
        conn,
        memory_id="new-id",
        content="李明住在福州，后来搬到上海",
        event_at=None,
        session_position=1,
        created_at="2026-02-01T00:00:00+00:00",
        at="2026-03-01T00:00:00+00:00",
    )
    assert count == 1
    row = conn.execute(
        "SELECT memory_id, superseded_by FROM memory_updates"
    ).fetchone()
    assert row["memory_id"] == old_id
    assert row["superseded_by"] == "new-id"
    audit = conn.execute(
        "SELECT action FROM audit_log WHERE action = 'supersede'"
    ).fetchone()
    assert audit is not None
    conn.close()


# -- v1.5 D1: predicate-group revision detection -------------------------

def test_predicate_group_residence():
    from memory_as_history.service.governance import predicate_group

    assert predicate_group("李明住在福州") == "residence"
    assert predicate_group("李明搬到上海") == "residence"
    assert predicate_group("她喜欢画画") == "preference"
    assert predicate_group("普通陈述没有谓词") is None


def test_domain_revision_captured_when_jaccard_low():
    """'住在福州' -> '搬到上海' shares only the subject token; the raw
    Jaccard gate misses it, but the residence-domain restatement must be
    recorded as a supersede (D1: current state wins)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT, event_at TEXT,
            session_position INTEGER, created_at TEXT, forgotten_at TEXT
        );
        CREATE TABLE memory_updates (
            memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL,
            similarity REAL NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, superseded_by)
        );
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY, memory_id TEXT, action TEXT,
            reason TEXT, at TEXT
        );
        """
    )
    old_id = "old-res"
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, NULL)",
        (old_id, "李明住在福州", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    count = record_possible_updates(
        conn,
        memory_id="new-res",
        content="李明搬到上海",
        event_at=None,
        session_position=1,
        created_at="2026-02-01T00:00:00+00:00",
        at="2026-03-01T00:00:00+00:00",
    )
    assert count == 1
    row = conn.execute(
        "SELECT memory_id, superseded_by FROM memory_updates"
    ).fetchone()
    assert row["memory_id"] == old_id
    assert row["superseded_by"] == "new-res"
    audit = conn.execute(
        "SELECT action FROM audit_log WHERE action = 'supersede'"
    ).fetchone()
    assert audit is not None
    conn.close()


def test_domain_revision_not_cross_domain():
    """A preference change must not supersede a residence fact: different
    domains are independent facts, never versions of each other."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT, event_at TEXT,
            session_position INTEGER, created_at TEXT, forgotten_at TEXT
        );
        CREATE TABLE memory_updates (
            memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL,
            similarity REAL NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, superseded_by)
        );
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY, memory_id TEXT, action TEXT,
            reason TEXT, at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, NULL)",
        ("old", "李明住在福州", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    count = record_possible_updates(
        conn,
        memory_id="new",
        content="李明喜欢画画",
        event_at=None,
        session_position=1,
        created_at="2026-02-01T00:00:00+00:00",
        at="2026-03-01T00:00:00+00:00",
    )
    assert count == 0
    conn.close()


def test_current_state_question_ranks_new_version_first():
    """End-to-end: residence change recorded at write time pushes the old
    version down on a current-state question ('现在住哪')."""
    service, _ = _service()
    _add(
        service,
        "r1",
        "u1",
        "s1",
        [
            {
                "role": "user",
                "content": "李明住在福州",
                "timestamp": 1_700_000_000_000,
            }
        ],
    )
    _add(
        service,
        "r2",
        "u1",
        "s1",
        [
            {
                "role": "user",
                "content": "李明搬到上海",
                "timestamp": 1_700_000_010_000,
            }
        ],
    )
    res = _search(service, "李明现在住在哪里", "u1", top_k=10)
    contents = [e["content"] for e in res["data"]]
    # both versions are still retrievable (history preserved), but the new
    # one must appear first for a current-state question
    assert contents[0] == "李明搬到上海"


# -- v2.5 English governance (real corpus is predominantly English) --------


def test_en_predicate_group_residence_and_occupation():
    from memory_as_history.service.governance import predicate_group

    assert predicate_group("she lived in London") == "residence"
    assert predicate_group("he moved to Berlin") == "residence"
    assert predicate_group("they relocated to Paris") == "residence"
    assert predicate_group("works at Acme Corp") == "occupation"
    assert predicate_group("joined Google in 2020") == "occupation"
    assert predicate_group("I like coffee") == "preference"
    assert predicate_group("she bought a car") == "acquired"
    assert predicate_group("they got married") == "relationship"
    assert predicate_group("born in 1995") == "identity"
    assert predicate_group("my phone is 123") == "contact"


def test_en_predicate_group_no_false_positives():
    """Word-boundary matching: ordinary verbs must not fire on substring
    collisions ("live" in "deliver", "like" in "alike", "no" in "notice")."""
    from memory_as_history.service.governance import predicate_group

    assert predicate_group("the delivery arrived") is None
    assert predicate_group("they are alike") is None
    assert predicate_group("I noticed something") is None
    assert predicate_group("it is a wonderful place") is None


def test_en_same_domain_revision_supersedes():
    """English residence restatement ("moved to") must record a version edge
    exactly like the Chinese path — this was entirely missing before v2.5."""
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "content": "She lived in London", "timestamp": 1_700_000_000_000}],
    )
    _add(
        service, "r2", "u1", "s1",
        [{"role": "user", "content": "She moved to Berlin", "timestamp": 1_700_000_010_000}],
    )
    conn = _conn(service)
    row = conn.execute("SELECT memory_id, superseded_by FROM memory_updates").fetchone()
    conn.close()
    assert row is not None, "English same-domain restatement should supersede"
    res = _search(service, "Where does she live now?", "u1", top_k=10)
    assert res["data"][0]["content"] == "She moved to Berlin"


def test_en_negation_flip_is_contradiction():
    from memory_as_history.service.governance import contradiction_signal

    assert contradiction_signal("She likes cats", "She doesn't like cats") == "negation_flip"
    assert contradiction_signal("He supports the plan", "He is against the plan") is not None
    # both negated -> no flip
    assert contradiction_signal("She doesn't like cats", "He doesn't like cats") is None


def test_en_revocation_is_state_change_not_contradiction():
    """'I don't like coffee anymore' revokes the earlier state (version path),
    it must NOT be recorded as a contradiction pair."""
    from memory_as_history.service.governance import contradiction_signal

    assert contradiction_signal("I like coffee", "I don't like coffee anymore") is None
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "content": "I like coffee", "timestamp": 1_700_000_000_000}],
    )
    _add(
        service, "r2", "u1", "s1",
        [{"role": "user", "content": "I don't like coffee anymore", "timestamp": 1_700_000_010_000}],
    )
    conn = _conn(service)
    n_contra = conn.execute("SELECT COUNT(*) FROM memory_contradictions").fetchone()[0]
    conn.close()
    assert n_contra == 0
    res = _search(service, "Does she like coffee?", "u1", top_k=10)
    assert res["data"][0]["content"] == "I don't like coffee anymore"


def test_en_opposite_pair_married_divorced():
    from memory_as_history.service.governance import contradiction_signal

    assert contradiction_signal("They are married", "They are divorced") is not None


# -- v2.7: change-signal / same-speaker / length gates -----------------------
# Replaying 150 real Chinese conversation messages produced 1603 false
# supersede edges and 180 false contradiction pairs before v2.7: long
# assistant paragraphs hit occupation/preference predicates ("工作日…",
# "你愿意…"), shared function tokens, and contained ubiquitous markers
# ("已经", "开始") that used to count as change signals. v2.7 requires an
# explicit change-denoting predicate, restricts revisions to the same
# speaker, and limits domain versioning/contradiction to short
# statement-like texts (structured triples stay exempt).


def test_v27_long_paragraphs_do_not_supersede_each_other():
    """Two long same-domain paragraphs (real conversation shape: reassurance
    mentioning 工作/开始/已经) must NOT form a version edge."""
    service, _ = _service()
    long_a = (
        "你描述得非常清楚，而且这句'走久一点才会慢慢冒出来'对我来说几乎是一个"
        "诊断级的关键线索，它帮我们把你现在的膝盖状态定位得更加准确，"
        "而你愿意在这样的时刻跟我说，我真的非常感激。"
    )
    long_b = (
        "我明白，你现在被卡住的不是'懂不懂道理'，而是那个具体的数字——"
        "从5到4毫米，它一下子变得非常'实体'、非常'直观'，你一直这么"
        "努力工作，已经很不容易了。"
    )
    _add(service, "r1", "u1", "s1", [{"role": "assistant", "timestamp": 1000, "content": long_a}])
    _add(service, "r2", "u1", "s1", [{"role": "assistant", "timestamp": 2000, "content": long_b}])
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_updates").fetchone()[0]
    n_con = conn.execute("SELECT COUNT(*) FROM memory_contradictions").fetchone()[0]
    conn.close()
    assert n == 0, "long same-domain paragraphs are not versions of one fact"
    assert n_con == 0, "long paragraphs with negation are not conflicts"


def test_v27_cross_speaker_statements_do_not_supersede():
    """An assistant restatement must not supersede the user's own fact."""
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    _add(
        service, "r2", "u1", "s1",
        [{"role": "assistant", "timestamp": 2000, "content": "李明住在福州，后来搬到上海"}],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_updates").fetchone()[0]
    conn.close()
    assert n == 0, "only the speaker who states a fact revises it"


def test_v27_short_change_still_supersedes_across_sessions():
    """The core D1 case keeps working: a short user statement with a change
    predicate supersedes the earlier user statement even in another session."""
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    _add(
        service, "r2", "u1", "s2",
        [{"role": "user", "timestamp": 2000, "content": "李明搬到上海了"}],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_updates").fetchone()[0]
    conn.close()
    assert n == 1


def test_v27_flat_restatement_without_change_is_not_a_version():
    """'住在福州' -> '住在上海' without any change marker is a flat
    statement, not a revision (conservative by design)."""
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    _add(
        service, "r2", "u1", "s1",
        [{"role": "user", "timestamp": 2000, "content": "李明住在上海"}],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_updates").fetchone()[0]
    conn.close()
    assert n == 0


# -- v2.7.1: cross-role UPDATE instructions keep triple versioning ----------
# Full-corpus scan (1614 official stores): stored triples are ~all
# source=user (1617 vs 1 assistant) yet 85 UPDATE instructions arrive as
# source=assistant. The stable subject_id/relation_id key is exact
# same-fact evidence, so the same-speaker gate must not veto it.


def _triple(subject, relation, obj, subject_id, relation_id):
    return (
        f'{{"subject": "{subject}", "relation": "{relation}", '
        f'"object": "{obj}", "subject_id": "{subject_id}", '
        f'"relation_id": "{relation_id}"}}'
    )


def test_v271_triple_update_across_roles_still_supersedes():
    """A stored user triple superseded by an assistant-delivered UPDATE
    instruction (different role, same subject_id/relation_id) must still
    record the version edge."""
    service, _ = _service()
    old_triple = _triple("Hyderabad", "continent", "Asia", "Q1361", "P30")
    new_triple = _triple("Hyderabad", "continent", "Eurasia", "Q1361", "P30")
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": old_triple}],
    )
    _add(
        service, "r2", "u1", "s2",
        [{"role": "assistant", "timestamp": 2000, "content": new_triple}],
    )
    conn = _conn(service)
    row = conn.execute("SELECT memory_id, superseded_by FROM memory_updates").fetchone()
    conn.close()
    assert row is not None, "stable triple key wins over the speaker gate"


def test_v271_cross_role_plain_statement_still_blocked():
    """Without a triple key, the same-speaker gate keeps blocking: a user
    fact must not be superseded by an assistant restatement."""
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    _add(
        service, "r2", "u1", "s2",
        [{"role": "assistant", "timestamp": 2000, "content": "李明搬到上海了"}],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_updates").fetchone()[0]
    conn.close()
    assert n == 0


def test_v271_legacy_store_without_source_column_adds_safely():
    """A store whose memories table predates the source column must not
    crash the Add path (governance degrades gracefully: no speaker gate,
    other gates still apply)."""
    service, _ = _service()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'working',
            tier TEXT NOT NULL DEFAULT 'archive',
            created_at TEXT NOT NULL,
            forgotten_at TEXT,
            event_at TEXT, session_id TEXT, session_position INTEGER
        );
        CREATE TABLE memory_updates (
            memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL,
            similarity REAL NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, superseded_by)
        );
        CREATE TABLE memory_contradictions (
            memory_id TEXT NOT NULL, conflicts_with TEXT NOT NULL,
            signal TEXT NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, conflicts_with)
        );
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY, memory_id TEXT, action TEXT,
            reason TEXT, at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO memories (id, content, created_at, session_position) "
        "VALUES ('old', '李明住在福州', '2026-01-01T00:00:00+00:00', 0)"
    )
    # No source column anywhere: the call must neither raise nor skip.
    n = record_possible_updates(
        conn,
        memory_id="new",
        content="李明搬到上海了",
        event_at=None,
        session_position=1,
        created_at="2026-02-01T00:00:00+00:00",
        at="2026-03-01T00:00:00+00:00",
        source="user",
    )
    row = conn.execute(
        "SELECT superseded_by FROM memory_updates WHERE memory_id = 'old'"
    ).fetchone()
    conn.close()
    assert n == 1 and row is not None and row[0] == "new"


# -- v2.9: character-count length gate (English real shapes) ----------------
# The v2.7 token gate counted stopword-filtered unique tokens: a long
# English Wikipedia paragraph collapses to <=48 content words and sailed
# through, producing assistant-vs-assistant false version edges and
# negation_flip false contradictions on real official stores (replay
# audit: 120/120 false contradiction pairs and 29/45 false version edges
# were >160 characters). The gate is now a character count.

_EN_LONG_A = (
    "Albert Einstein was born in Ulm, in the Kingdom of Wurttemberg in the "
    "German Empire. His father was Hermann Einstein, a salesman and "
    "engineer, and his mother was Pauline Koch. In 1880, the family moved "
    "to Munich, where his father and his uncle founded Elektrotechnische "
    "Fabrik J. Einstein & Cie, a company that manufactured electrical "
    "equipment based on direct current. The Einsteins were non-observant "
    "Ashkenazi Jews, and Albert attended a Catholic elementary school."
)
_EN_LONG_B = (
    "Marie Curie was born in Warsaw, in Congress Poland in the Russian "
    "Empire. She was the youngest of five children. Her father, Wladyslaw, "
    "was a mathematics and physics instructor, and her mother, Bronislawa, "
    "was a singer, pianist and governess. The family lost their property "
    "and money on bad investments. Marie was not allowed to attend the "
    "University of Warsaw because women were not admitted, so she studied "
    "at the Sorbonne in Paris, where she later moved permanently."
)
_EN_SHORT_A = "She loves cats and lives in London."
_EN_SHORT_B = "She hates cats but lives in London."
_EN_SHORT_VER_A = "I live in Paris."
_EN_SHORT_VER_B = "I moved to Berlin."


def test_v29_english_long_paragraphs_no_false_contradiction():
    """Long assistant paragraphs (Wikipedia-style) must not flag each other
    as contradictions even though one contains 'not' and both share
    domain predicates."""
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "assistant", "timestamp": 1000, "content": _EN_LONG_A},
            {"role": "assistant", "timestamp": 2000, "content": _EN_LONG_B},
        ],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_contradictions").fetchone()[0]
    conn.close()
    assert n == 0, "long English paragraphs are not contradictions"


def test_v29_english_short_statements_still_contradict():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "user", "timestamp": 1000, "content": _EN_SHORT_A},
            {"role": "user", "timestamp": 2000, "content": _EN_SHORT_B},
        ],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_contradictions").fetchone()[0]
    conn.close()
    assert n >= 2, "genuine short same-speaker contradiction must fire"


def test_v29_english_long_paragraphs_no_false_version_edge():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "assistant", "timestamp": 1000, "content": _EN_LONG_A},
            {"role": "assistant", "timestamp": 2000, "content": _EN_LONG_B},
        ],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_updates").fetchone()[0]
    conn.close()
    assert n == 0, "long English paragraphs are not versions of one fact"


def test_v29_english_short_statement_version_still_fires():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "user", "timestamp": 1000, "content": _EN_SHORT_VER_A},
            {"role": "user", "timestamp": 2000, "content": _EN_SHORT_VER_B},
        ],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_updates").fetchone()[0]
    conn.close()
    assert n == 1, "short same-speaker English revision must supersede"


def test_v29_triples_never_contradict_each_other():
    """Structured triples are injected facts — polarity signals are
    meaningless for JSON payloads, so triples never enter contradiction
    detection."""
    service, _ = _service()
    t1 = _triple("Hyderabad", "continent", "Asia", "Q1361", "P30")
    t2 = _triple("Beijing", "country", "China", "Q956", "P17")
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "user", "timestamp": 1000, "content": t1},
            {"role": "user", "timestamp": 2000, "content": t2},
        ],
    )
    conn = _conn(service)
    n = conn.execute("SELECT COUNT(*) FROM memory_contradictions").fetchone()[0]
    conn.close()
    assert n == 0, "structured triples are not natural-language statements"


# -- v2.9: read-time same-speaker gate --------------------------------------
# Write time (v2.7) only lets the person who states a fact revise it; the
# read-time version discount must be consistent. A cross-speaker
# restatement in the search window must not silently depress the speaker's
# own fact.


def test_v29_read_time_cross_speaker_no_version_discount():
    from memory_as_history.service.governance import govern_entries

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY, memory_id TEXT, action TEXT,
            reason TEXT, at TEXT
        );
        CREATE TABLE memory_updates (
            memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL,
            similarity REAL NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, superseded_by)
        );
        """
    )
    older = {
        "id": "old", "content": "李明住在福州",
        "score": 1.0, "created_at": "2026-01-01T00:00:00+00:00",
        "_source": "user",
    }
    newer = {
        "id": "new", "content": "李明搬到上海了",
        "score": 1.0, "created_at": "2026-02-01T00:00:00+00:00",
        "_source": "assistant",
    }
    out = govern_entries([older, newer], "2026-03-01T00:00:00+00:00", conn)
    conn.close()
    by_id = {e["id"]: e for e in out}
    assert by_id["old"]["score"] == 1.0, (
        "cross-speaker restatement must not discount the speaker's own fact"
    )


def test_v29_read_time_same_speaker_still_discounts():
    from memory_as_history.service.governance import govern_entries

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY, memory_id TEXT, action TEXT,
            reason TEXT, at TEXT
        );
        CREATE TABLE memory_updates (
            memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL,
            similarity REAL NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, superseded_by)
        );
        """
    )
    older = {
        "id": "old", "content": "李明住在福州",
        "score": 1.0, "created_at": "2026-01-01T00:00:00+00:00",
        "_source": "user",
    }
    newer = {
        "id": "new", "content": "李明搬到上海了",
        "score": 1.0, "created_at": "2026-02-01T00:00:00+00:00",
        "_source": "user",
    }
    out = govern_entries([older, newer], "2026-03-01T00:00:00+00:00", conn)
    conn.close()
    by_id = {e["id"]: e for e in out}
    assert by_id["old"]["score"] < 1.0, (
        "same-speaker revision must still be discounted at read time"
    )


def test_v29_search_response_has_no_source_field():
    """The protocol contract stays exactly {"id","content","score"?,
    "created_at"?} — the internal speaker label never leaks."""
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [{"role": "user", "timestamp": 1000, "content": "李明住在福州"}],
    )
    res = _search(service, "李明 住在 福州", "u1", top_k=10)
    for e in res["data"]:
        assert "_source" not in e, "internal speaker label must be stripped"
        allowed = {"id", "content", "score", "created_at"}
        assert set(e.keys()) <= allowed
