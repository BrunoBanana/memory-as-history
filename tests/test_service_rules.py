"""Tests for v1.5 G2 rule memories (write-time marking + read-time surfacing).

Rule statements ("家里规定不许养猫", "我习惯每天跑步") must be
recognized at write time so rule questions ("我可以养猫吗", "有什么规矩")
can surface them — a question like "有什么规矩" shares no token with the
statements themselves, so BM25 alone would miss them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memory_as_history.service import MemoryService
from memory_as_history.service.memory_service import (
    _is_rule_question,
    _is_rule_statement,
)


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="mem-rule-")),
        search_mode="lexical",
    )


def _add(svc, user_id, session_id, texts, request_id="r1"):
    msgs = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": text,
            "timestamp": 1_700_000_000_000 + i * 1000,
        }
        for i, text in enumerate(texts)
    ]
    return svc.add(
        {
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "messages": msgs,
        }
    )


# -- detection -----------------------------------------------------------

def test_rule_statement_detection():
    assert _is_rule_statement("家里规定不许养猫")
    assert _is_rule_statement("我们约定每周三开例会")
    assert _is_rule_statement("她习惯每天早上跑步")
    assert _is_rule_statement("这里禁止吸烟")
    assert _is_rule_statement("奶奶说吃饭时必须等长辈先动筷")
    # ordinary facts must not be flagged
    assert _is_rule_statement("她住在杭州") is False
    assert _is_rule_statement("我喜欢吃苹果") is False
    assert _is_rule_statement("今天天气很好") is False


def test_rule_question_detection():
    assert _is_rule_question("我可以养猫吗")
    assert _is_rule_question("家里有什么规矩")
    assert _is_rule_question("公司规定加班有补贴吗")
    assert _is_rule_question("能不能在办公室吃东西")
    assert _is_rule_question("她住在哪里") is False
    assert _is_rule_question("她喜欢什么颜色") is False


# -- write-time marking --------------------------------------------------

def test_rule_statement_marked_in_db():
    svc = _service()
    _add(svc, "u1", "s1", ["家里规定不许养猫", "她住在杭州"])
    store = svc._user_store("u1")
    rows = store._conn.execute(
        "SELECT content, material_type FROM memories ORDER BY session_position"
    ).fetchall()
    by_content = {r["content"]: r["material_type"] for r in rows}
    assert by_content["家里规定不许养猫"] == "rule"
    assert by_content["她住在杭州"] == "unspecified"
    store.close()


# -- read-time surfacing -------------------------------------------------

def test_rule_question_surfaces_rule_first():
    svc = _service()
    _add(svc, "u1", "s1", ["家里规定不许养猫", "她住在杭州", "她喜欢画画"])
    res = svc.search(
        {"query": "我可以养猫吗", "user_id": "u1", "top_k": 10}
    )
    data = res["data"]
    assert len(data) >= 1
    assert data[0]["content"] == "家里规定不许养猫"
    assert data[0]["score"] == 999.0


def test_rule_surfacing_newest_first():
    svc = _service()
    # later rule supersedes the earlier one for current-state questions
    _add(svc, "u1", "s1", ["以前规定不许养猫"], request_id="r1")
    _add(svc, "u1", "s2", ["现在规定可以养猫了"], request_id="r2")
    res = svc.search(
        {"query": "现在能不能养猫", "user_id": "u1", "top_k": 10}
    )
    assert res["data"][0]["content"] == "现在规定可以养猫了"


def test_plain_query_no_rule_surfacing():
    svc = _service()
    _add(svc, "u1", "s1", ["家里规定不许养猫", "她住在杭州"])
    res = svc.search({"query": "她住在哪里", "user_id": "u1", "top_k": 10})
    # rule entries have score 999; a plain question must not surface them
    assert all(r.get("score", 1.0) < 999.0 for r in res["data"])
