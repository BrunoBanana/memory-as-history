"""Tests for v2.2 C1 relative-time anchoring.

"最近/上周/yesterday" questions lift the newest slice of evidence, so a
memory that only carries a recent timestamp ("我搬到上海了") answers
instead of an older one that shares more words with the question.
Plain questions keep relevance ranking.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory_as_history.service import MemoryService
from memory_as_history.service.memory_service import _recent_time_boost

BASE = 1_700_000_000_000


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="mem-v22-")),
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


def test_recent_trigger_detection():
    # single entry = the newest third -> boosted
    assert _recent_time_boost("她最近住在哪里", [{"id": "a", "content": "x", "score": 0.5}], {"a": 1.0})[0]["score"] == 0.65
    assert _recent_time_boost("what did she do yesterday", [{"id": "a", "content": "x", "score": 0.5}], {"a": 1.0})[0]["score"] == 0.65
    # no trigger -> untouched
    assert _recent_time_boost("她住在哪里", [{"id": "a", "content": "x", "score": 0.5}], {"a": 1.0})[0]["score"] == 0.5


def test_recent_boost_lifts_newest():
    # newest memory shares fewer words with the query than the older one;
    # the recency lift must put it first
    svc = _service()
    _add(svc, "u1", "s1", ["我住在福州。"], request_id="r1", base_ts=BASE)
    _add(svc, "u1", "s2", ["我搬到上海了。"], request_id="r2", base_ts=BASE + 100_000)
    res = svc.search({"query": "她最近住在哪里", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert contents[0].startswith("我搬到上海了")


def test_plain_question_keeps_relevance():
    svc = _service()
    _add(svc, "u1", "s1", ["我住在福州。"], request_id="r1", base_ts=BASE)
    _add(svc, "u1", "s2", ["我搬到上海了。"], request_id="r2", base_ts=BASE + 100_000)
    res = svc.search({"query": "她住在哪里", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert contents[0].startswith("我住在福州")


def test_contract_clean():
    svc = _service()
    _add(svc, "u1", "s1", ["我住在福州。"], request_id="r1", base_ts=BASE)
    _add(svc, "u1", "s2", ["我搬到上海了。"], request_id="r2", base_ts=BASE + 100_000)
    res = svc.search({"query": "她最近住在哪里", "user_id": "u1", "top_k": 10})
    for item in res["data"]:
        assert set(item) <= {"id", "content", "score", "created_at"}
