"""Tests for v2.3 I1 cross-session preference aggregation.

A generic preference question ("她喜欢什么 / what does she like")
aggregates preference-domain memories expressed across sessions in
different words; a specific one ("她喜欢猫吗") still ranks its BM25 hit
first. Non-preference questions never trigger the append.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory_as_history.aml import MemoryService
from memory_as_history.aml.memory_service import _preference_qa_append

BASE = 1_700_000_000_000


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="aml-v23-")),
        search_mode="lexical",
    )


def _add(svc, user_id, session_id, texts, request_id="r1", base_ts=BASE):
    msgs = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": text,
            "timestamp": base_ts + i * 1000,
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


def test_trigger_detection():
    appended = _preference_qa_append("她喜欢什么", [{"id": "a", "content": "x", "score": 0.5}], None)
    assert appended == [{"id": "a", "content": "x", "score": 0.5}]  # no conn -> no-op
    untouched = _preference_qa_append("她住在哪里", [{"id": "a", "content": "x", "score": 0.5}], object())
    assert len(untouched) == 1


def test_generic_preference_question_aggregates_sessions():
    svc = _service()
    _add(svc, "u1", "s1", ["我喜欢猫。"], request_id="r1", base_ts=BASE)
    _add(svc, "u1", "s2", ["我爱吃火锅。"], request_id="r2", base_ts=BASE + 10_000)
    res = svc.search({"query": "她喜欢什么", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert any("猫" in c for c in contents)
    assert any("火锅" in c for c in contents)


def test_specific_preference_question_keeps_hit_first():
    svc = _service()
    _add(svc, "u1", "s1", ["我喜欢猫。"], request_id="r1", base_ts=BASE)
    _add(svc, "u1", "s2", ["我爱吃火锅。"], request_id="r2", base_ts=BASE + 10_000)
    res = svc.search({"query": "她喜欢猫吗", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert contents[0].startswith("我喜欢猫")


def test_non_preference_question_not_affected():
    svc = _service()
    _add(svc, "u1", "s1", ["我喜欢猫。"], request_id="r1", base_ts=BASE)
    _add(svc, "u1", "s2", ["我爱吃火锅。"], request_id="r2", base_ts=BASE + 10_000)
    res = svc.search({"query": "她住在哪里", "user_id": "u1", "top_k": 10})
    # no preference evidence pollutes a residence question
    assert all("喜欢" not in e["content"] and "火锅" not in e["content"] for e in res["data"])


def test_english_generic_preference():
    svc = _service()
    _add(svc, "u1", "s1", ["I love cats."], request_id="r1", base_ts=BASE)
    _add(svc, "u1", "s2", ["I enjoy hotpot."], request_id="r2", base_ts=BASE + 10_000)
    res = svc.search({"query": "what does she like", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert any("cats" in c for c in contents)
    assert any("hotpot" in c for c in contents)
