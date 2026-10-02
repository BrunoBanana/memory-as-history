"""Tests for the AML governance layer (v0.2).

Deterministic, model-free memory governance:

- write-time update detection records ``superseded_by`` edges in
  ``aml_updates`` (audited, non-destructive);
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

from memory_as_history.aml import MemoryService
from memory_as_history.aml.governance import (
    _version_decision,
    govern_entries,
    is_revision,
    jaccard,
    overlap_signals,
    record_possible_updates,
    token_set,
)


def _service() -> tuple[MemoryService, Path]:
    data_dir = Path(tempfile.mkdtemp(prefix="aml-gov-"))
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
            "SELECT memory_id, superseded_by FROM aml_updates"
        ).fetchall()
        audits = conn.execute(
            "SELECT action FROM audit_log WHERE action = 'aml_supersede'"
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
        n = conn.execute("SELECT COUNT(*) AS n FROM aml_updates").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0


def test_exact_duplicate_is_not_recorded_as_update():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "完全一样的内容"}])
    _add(service, "r2", "u1", "s1", [{"role": "user", "content": "完全一样的内容"}])
    conn = _conn(service)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM aml_updates").fetchone()["n"]
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
        n = conn.execute("SELECT COUNT(*) AS n FROM aml_updates").fetchone()["n"]
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
        CREATE TABLE aml_updates (
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
        "SELECT memory_id, superseded_by FROM aml_updates"
    ).fetchone()
    assert row["memory_id"] == old_id
    assert row["superseded_by"] == "new-id"
    audit = conn.execute(
        "SELECT action FROM audit_log WHERE action = 'aml_supersede'"
    ).fetchone()
    assert audit is not None
    conn.close()
