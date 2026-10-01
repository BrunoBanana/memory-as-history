"""Tests for AML read-time neighbor expansion (P0 evidence boost).

Multi-evidence questions ("where does X live?" when a later turn says
"moved to Y") need more than one turn. Neighbor expansion adds nearby
turns of every ranked seed — but only turns sharing a query token, so
unrelated filler never pollutes the evidence window, and the Top K budget
always bounds the total. Expansion rows rank below BM25 seeds.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from memory_as_history.aml import MemoryService


def _service() -> tuple[MemoryService, Path]:
    data_dir = Path(tempfile.mkdtemp(prefix="aml-exp-"))
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


# -- neighbor expansion recovers multi-evidence ---------------------------


def test_nearby_evidence_turns_are_expanded():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "user", "timestamp": 1000, "content": "陈静说她计划搬去上海"},
            {"role": "user", "timestamp": 2000, "content": "你觉得这个决定怎么样"},
            {"role": "user", "timestamp": 3000, "content": "她上周已经搬到上海了"},
        ],
    )
    # query mentions only the seed turn's key token; expansion must add the
    # evidence neighbor even though it is not directly scored by BM25,
    # while the unrelated filler turn ("你觉得这个决定怎么样") must NOT
    # enter the evidence window.
    result = _search(service, "陈静", "u1", top_k=10)
    contents = [item["content"] for item in result["data"]]
    assert "她上周已经搬到上海了" in contents
    assert not any("你觉得这个决定怎么样" in c for c in contents)
    # seeds rank above expansion rows
    seed_score = result["data"][0]["score"]
    for item in result["data"][1:]:
        assert item["score"] < seed_score


def test_expansion_only_adds_turns_sharing_a_query_token():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "user", "content": "王芳提到她喜欢摄影"},
            {"role": "user", "content": "天气真的很好我们去了公园"},
            {"role": "user", "content": "她的相机是富士牌的"},
        ],
    )
    result = _search(service, "摄影", "u1", top_k=10)
    contents = [item["content"] for item in result["data"]]
    # the unrelated middle turn shares no token with the query -> excluded
    assert not any("天气真的很好" in c for c in contents)
    # the neighbor that does share "相机" (not matched here) may be absent;
    # the point is the filler never appears.


def test_expansion_respects_top_k():
    service, _ = _service()
    messages = [
        {"role": "user", "content": f"话题轮次第{i}条关于旅行规划"}
        for i in range(20)
    ]
    _add(service, "r1", "u1", "s1", messages)
    result = _search(service, "旅行", "u1", top_k=5)
    assert len(result["data"]) <= 5
    result = _search(service, "旅行", "u1", top_k=100)
    assert len(result["data"]) == 20  # expansion never grows beyond stored data


def test_expansion_keeps_contract_clean():
    service, _ = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "user", "content": "李明提到他明年去东京"},
            {"role": "user", "content": "他已经在订机票了"},
        ],
    )
    result = _search(service, "李明 东京", "u1", top_k=10)
    for item in result["data"]:
        assert set(item) <= {"id", "content", "score", "created_at"}
        assert isinstance(item["score"], float)


def test_expansion_is_audited():
    service, data_dir = _service()
    _add(
        service, "r1", "u1", "s1",
        [
            {"role": "user", "content": "赵磊说他买了新车"},
            {"role": "user", "content": "他周末提了这辆新车"},
        ],
    )
    _search(service, "赵磊", "u1", top_k=10)
    conn = sqlite3.connect(str(data_dir / "users" / f"{_hash('u1')}.db"))
    try:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE action = 'aml_turn_expand'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "expansion decisions must be auditable"


def _hash(user_id: str) -> str:
    import hashlib

    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
