"""Tests for v1.9 C2 order-question rerank.

Sequence questions ("先后/顺序/按时间/timeline") must hand the answer
generator the story in the order it happened. Evidence is reordered by
message time ascending; ordinary questions keep relevance ranking.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory_as_history.aml import MemoryService
from memory_as_history.aml.memory_service import _is_order_query


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="aml-v19-")),
        search_mode="lexical",
    )


def _add(svc, user_id, session_id, texts, request_id="r1", base_ts=1_700_000_000_000):
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


def test_order_query_detection():
    for q in ("按时间顺序说说我们合作的过程", "这件事的先后顺序",
              "what happened in chronological order", "tell me the timeline"):
        assert _is_order_query(q), q


def test_ordinary_query_not_order():
    assert not _is_order_query("她现在住在哪里")
    assert not _is_order_query("what is her phone number")


def test_order_question_returns_chronological_evidence():
    svc = _service()
    _add(svc, "u1", "s1",
         ["2021年我住在福州。", "2023年我搬到上海。"],
         request_id="r1")
    res = svc.search(
        {"query": "按时间顺序说说我住的地方的变化", "user_id": "u1", "top_k": 10}
    )
    contents = [e["content"] for e in res["data"]]
    fz = contents.index(next(c for c in contents if "福州" in c))
    sh = contents.index(next(c for c in contents if "上海" in c))
    assert fz < sh  # 2021 before 2023


def test_non_order_question_keeps_relevance_ranking():
    svc = _service()
    _add(svc, "u1", "s1",
         ["2021年我住在福州。", "2023年我搬到上海。"],
         request_id="r1")
    res = svc.search(
        {"query": "我2023年住在哪里", "user_id": "u1", "top_k": 10}
    )
    contents = [e["content"] for e in res["data"]]
    # year-anchored boost must still float the 2023 statement first
    assert contents[0].startswith("2023年我搬到上海")
