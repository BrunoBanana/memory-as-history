"""Contract tests for the AML Add/Search adapter (text track).

These assert the fixed AML contract (Cycle 2 API Guide) at the service
layer: synchronous write visibility, request_id idempotency, strict user_id
isolation, top_k bounding, structured errors, streaming interleaving, and
read compatibility with the project Store.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from memory_as_history.aml import MemoryService
from memory_as_history.aml.contract import ContractError
from memory_as_history.storage import Store


def _service() -> tuple[MemoryService, Path]:
    data_dir = Path(tempfile.mkdtemp(prefix="aml-test-"))
    return MemoryService(data_dir=data_dir), data_dir


def _add(service, request_id, user_id, session_id, messages, **extra):
    body = {
        "request_id": request_id,
        "user_id": user_id,
        "session_id": session_id,
        "messages": messages,
        **extra,
    }
    return service.add(body)


def _search(service, query, user_id, top_k=10, **extra):
    body = {"query": query, "user_id": user_id, "top_k": top_k, **extra}
    return service.search(body)


# -- Add: synchronous visibility ----------------------------------------


def test_add_returns_echo_and_search_sees_it_immediately():
    service, _ = _service()
    resp = _add(
        service,
        "r1",
        "u1",
        "s1",
        [{"role": "user", "content": "李明住在福州"}],
    )
    assert resp == {
        "success": True,
        "request_id": "r1",
        "user_id": "u1",
        "session_id": "s1",
    }
    result = _search(service, "李明住在哪", "u1")
    assert result["data"], "a memory written by Add must be immediately searchable"
    assert result["data"][0]["content"] == "李明住在福州"
    assert result["data"][0]["id"]
    assert isinstance(result["data"][0]["score"], float)
    assert result["data"][0]["created_at"].endswith("Z")


def test_empty_search_returns_empty_data_array():
    service, _ = _service()
    assert _search(service, "anything", "u1") == {"data": []}


def test_add_orders_messages_within_session():
    service, _ = _service()
    _add(
        service,
        "r1",
        "u1",
        "s1",
        [
            {"role": "user", "content": "第一条消息"},
            {"role": "user", "content": "第二条消息"},
        ],
    )
    # positions are assigned in message order via the aml_sessions counter
    db = service._open_connection("u1")
    try:
        rows = db.execute(
            "SELECT content, session_position FROM memories "
            "WHERE session_id = 's1' ORDER BY session_position"
        ).fetchall()
    finally:
        db.close()
    assert [r["content"] for r in rows] == ["第一条消息", "第二条消息"]
    assert [r["session_position"] for r in rows] == [0, 1]


# -- Idempotency ----------------------------------------------------------


def test_same_request_id_replay_does_not_duplicate():
    service, data_dir = _service()
    msg = {"role": "user", "content": "幂等测试内容"}
    first = _add(service, "rid", "u1", "s1", [msg])
    second = _add(service, "rid", "u1", "s1", [msg])
    assert first == second
    conn = service._open_connection("u1")
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
    finally:
        conn.close()
    assert count == 1
    # the second call must not have advanced the session counter either
    conn2 = service._open_connection("u1")
    try:
        next_pos = conn2.execute(
            "SELECT next_position FROM aml_sessions WHERE session_id = 's1'"
        ).fetchone()
    finally:
        conn2.close()
    assert next_pos is not None and next_pos["next_position"] == 1


def test_idempotency_survives_interleaved_other_writes():
    service, _ = _service()
    msg = {"role": "user", "content": "要幂等的内容"}
    _add(service, "rid", "u1", "s1", [msg])
    _add(service, "other", "u1", "s1", [{"role": "user", "content": "别的消息"}])
    _add(service, "rid", "u1", "s1", [msg])  # replay after other writes
    conn = service._open_connection("u1")
    try:
        rows = conn.execute("SELECT content FROM memories").fetchall()
    finally:
        conn.close()
    assert len(rows) == 2


# -- user_id isolation -----------------------------------------------------


def test_users_are_physically_isolated():
    service, data_dir = _service()
    _add(service, "r1", "user-alpha", "sa", [{"role": "user", "content": "阿尔法用户的秘密"}])
    result = _search(service, "阿尔法用户的秘密", "user-beta")
    assert result == {"data": []}
    # and the reverse direction stays intact
    assert _search(service, "阿尔法用户的秘密", "user-alpha")["data"]


def test_user_dbs_are_separate_files():
    service, data_dir = _service()
    _add(service, "r1", "u-alpha", "s", [{"role": "user", "content": "x"}])
    _add(service, "r2", "u-beta", "s", [{"role": "user", "content": "y"}])
    files = sorted(p.name for p in (data_dir / "users").glob("*.db"))
    assert len(files) == 2


# -- top_k bounding ---------------------------------------------------------


def test_search_respects_top_k():
    service, _ = _service()
    messages = [
        {"role": "user", "content": f"记忆内容第{i}条，包含关键词芒果"}
        for i in range(30)
    ]
    _add(service, "r1", "u1", "s1", messages)
    result = _search(service, "芒果", "u1", top_k=10)
    assert len(result["data"]) == 10
    result = _search(service, "芒果", "u1", top_k=100)
    assert len(result["data"]) == 30  # fewer than top_k is fine


def test_unrelated_memories_are_filtered_out_of_search():
    service, _ = _service()
    _add(
        service,
        "r1",
        "u1",
        "s1",
        [{"role": "user", "content": "我在杭州做后端开发，喜欢片儿川"}],
    )
    # A query sharing no token with any stored memory must not return it:
    # zero-relevance evidence would only pollute the answer generator.
    result = _search(service, "量子物理与意大利歌剧", "u1", top_k=5)
    assert result["data"] == []
    # A query with a lexical hit still returns the memory.
    result = _search(service, "杭州 后端", "u1", top_k=5)
    assert len(result["data"]) == 1
    assert result["data"][0]["score"] > 0


def test_result_shape_matches_contract():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "唯一一条内容"}])
    result = _search(service, "唯一一条内容", "u1", top_k=5)
    assert isinstance(result, dict)
    assert isinstance(result["data"], list)
    for item in result["data"]:
        assert set(item) <= {"id", "content", "score", "created_at"}
        assert isinstance(item["id"], str) and item["id"]
        assert isinstance(item["content"], str) and item["content"]


# -- streaming interleaving -------------------------------------------------


def test_streaming_incremental_visibility():
    service, _ = _service()
    # event 1: write only the first chunk
    _add(
        service,
        "c0",
        "u1",
        "conv",
        [{"role": "user", "content": "事件一：我买了苹果电脑"}],
    )
    after_one = _search(service, "苹果", "u1")
    assert len(after_one["data"]) == 1
    # event 2: later chunk arrives
    _add(
        service,
        "c1",
        "u1",
        "conv",
        [{"role": "user", "content": "事件二：后来我退了苹果电脑，换了华为"}],
    )
    after_two = _search(service, "苹果", "u1")
    assert len(after_two["data"]) == 2
    # positions continue across chunks
    conn = service._open_connection("u1")
    try:
        positions = [
            r["session_position"]
            for r in conn.execute(
                "SELECT session_position FROM memories "
                "WHERE session_id = 'conv' ORDER BY session_position"
            )
        ]
    finally:
        conn.close()
    assert positions == [0, 1]


# -- timestamps and provenance -------------------------------------------------


def test_timestamp_becomes_event_at():
    service, _ = _service()
    _add(
        service,
        "r1",
        "u1",
        "s1",
        [{"role": "user", "timestamp": 1704067200000, "content": "带时间戳的内容"}],
    )
    conn = service._open_connection("u1")
    try:
        row = conn.execute(
            "SELECT event_at, source FROM memories LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row["event_at"].startswith("2024-01-01")
    assert row["source"] == "user"


# -- validation errors --------------------------------------------------------


def test_missing_request_id_is_422():
    service, _ = _service()
    with pytest.raises(ContractError) as exc:
        service.add(
            {"user_id": "u1", "session_id": "s1", "messages": [{"role": "user", "content": "x"}]}
        )
    assert exc.value.status == 422


def test_empty_messages_is_422():
    service, _ = _service()
    with pytest.raises(ContractError):
        service.add(
            {"request_id": "r", "user_id": "u1", "session_id": "s1", "messages": []}
        )


def test_blank_content_is_422():
    service, _ = _service()
    with pytest.raises(ContractError):
        _add(service, "r", "u1", "s1", [{"role": "user", "content": "   "}])


def test_bad_top_k_is_422():
    service, _ = _service()
    with pytest.raises(ContractError):
        service.search({"query": "q", "user_id": "u1", "top_k": 0})


def test_missing_query_is_422():
    service, _ = _service()
    with pytest.raises(ContractError):
        service.search({"user_id": "u1", "top_k": 5})


def test_non_object_payload_is_422():
    service, _ = _service()
    with pytest.raises(ContractError):
        service.add("not an object")


# -- read compatibility with the project Store --------------------------------


def test_adapter_rows_are_readable_by_project_store():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "项目存储也能读到这句话"}])
    store = Store(service.data_dir / "users" / f"{_hash('u1')}.db")
    try:
        result = store.recall("项目存储也能读到这句话", limit=5)
        contents = [m["content"] for m in result["memories"]]
        assert "项目存储也能读到这句话" in contents
    finally:
        store.close()


def _hash(user_id: str) -> str:
    import hashlib

    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]


# -- options pass-through -----------------------------------------------------


def test_options_are_accepted_and_do_not_leak():
    service, _ = _service()
    _add(service, "r1", "u1", "s1", [{"role": "user", "content": "选项测试"}])
    result = _search(service, "选项测试", "u1", top_k=5, options=["A. 选项一", "B. 选项二"])
    assert result["data"]
    for item in result["data"]:
        assert "A." not in item["content"] or item["content"].startswith("A.")


# -- no answer leakage ---------------------------------------------------------


def test_search_returns_memory_evidence_not_constructed_answers():
    service, _ = _service()
    _add(
        service,
        "r1",
        "u1",
        "s1",
        [{"role": "user", "content": "用户的密码是 abc123 请不要外传"}],
    )
    result = _search(service, "用户的密码是什么？请回答", "u1")
    for item in result["data"]:
        # evidence must be verbatim stored content, never a generated answer
        assert item["content"] == "用户的密码是 abc123 请不要外传"
