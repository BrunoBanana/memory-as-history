"""Tests for rolling summaries (F1: long-history global synthesis).

Review-style questions ("总结一下…", "Summarize the whole conversation")
must be able to draw on the entire history, not only the retrieved window.
We keep one rolling summary per session and surface it in front of the
ranked evidence — but only for queries that explicitly ask for a synthesis,
so ordinary factual questions are never affected.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memory_as_history.service import MemoryService
from memory_as_history.service.memory_service import (
    _point,
    _summary_clue,
    _update_summary,
)


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="mem-sum-")),
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

def test_summary_clue_detection():
    assert _summary_clue("总结一下她的经历")
    assert _summary_clue("回顾他们这五年的故事")
    assert _summary_clue("Summarize the whole conversation")
    assert _summary_clue("What happened over these years?")
    assert _summary_clue("她住在哪里") is False
    assert _summary_clue("What did she do yesterday?") is False
    assert _summary_clue("王磊养了什么宠物") is False


def test_point_extraction():
    assert _point("第一行\n第二行") == "第一行"
    assert _point("  ") == ""
    assert _point("x" * 200) == "x" * 160 + "…"


def test_update_summary_dedupe_and_roll():
    s = ""
    for i in range(20):
        s = _update_summary(s, f"point {i}")
    assert s.count("\n") == 19
    # dedupe: same point does not append twice
    assert _update_summary(s, "point 3") == s
    # truncation keeps the earliest head plus the latest tail
    long = _update_summary("", "长" * 5000)
    assert len(long) <= 4000
    assert long.startswith("长")
    assert "…" in long


# -- integration ---------------------------------------------------------

def test_summary_built_on_add():
    svc = _service()
    _add(svc, "u1", "s1", ["你好", "我住在杭州"])
    store = svc._user_store("u1")
    rows = store._conn.execute(
        "SELECT summary FROM memory_summaries"
    ).fetchall()
    assert len(rows) == 1
    assert "杭州" in rows[0]["summary"]
    store.close()


def test_review_query_gets_summary_entry():
    svc = _service()
    _add(svc, "u1", "s1", ["她喜欢画画", "她住在杭州", "她养了一只猫"])
    res = svc.search(
        {"query": "总结一下她的经历", "user_id": "u1", "top_k": 10}
    )
    data = res["data"]
    assert data[0]["id"].startswith("summary_")
    assert data[0]["score"] == 999.0
    assert "杭州" in data[0]["content"]
    # summary entry present; ranked evidence may or may not match a
    # review-style query (its tokens rarely hit specific facts)
    assert len(data) >= 1


def test_plain_query_no_summary():
    svc = _service()
    _add(svc, "u1", "s1", ["她喜欢画画", "她住在杭州"])
    res = svc.search({"query": "她住在哪里", "user_id": "u1", "top_k": 10})
    assert all(not str(r["id"]).startswith("summary_") for r in res["data"])


def test_idempotent_add_no_duplicate_points():
    svc = _service()
    _add(svc, "u1", "s1", ["她住在杭州"], request_id="r1")
    _add(svc, "u1", "s1", ["她住在杭州"], request_id="r1")  # replay
    store = svc._user_store("u1")
    row = store._conn.execute(
        "SELECT summary FROM memory_summaries"
    ).fetchone()
    assert row["summary"].count("杭州") == 1
    store.close()


def test_lazy_build_for_preexisting_data():
    # Data written before the summary feature must still synthesize on demand.
    svc = _service()
    _add(svc, "u1", "s1", ["她在上海工作", "她养了一条狗"])
    store = svc._user_store("u1")
    store._conn.execute("DROP TABLE memory_summaries")
    store._conn.execute(
        "CREATE TABLE memory_summaries (session_id TEXT PRIMARY KEY, "
        "summary TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    store._conn.commit()
    store.close()
    res = svc.search(
        {"query": "总结一下她的经历", "user_id": "u1", "top_k": 10}
    )
    assert res["data"][0]["id"].startswith("summary_")
    assert "上海" in res["data"][0]["content"]
