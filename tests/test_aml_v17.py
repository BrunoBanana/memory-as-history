"""Tests for v1.7 capability enhancements.

- F1: summary points carry more substance (160-char head, not 96).
- B2: causal questions ("为什么", "how did", "后来") widen the neighbor
  expansion radius so the chain of steps enters the evidence window.
- D1: contact-domain restatements (电话/号码/手机/邮箱/微信) supersede
  older versions like residence/occupation do.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory_as_history.aml import MemoryService
from memory_as_history.aml.governance import predicate_group, record_possible_updates
from memory_as_history.aml.memory_service import (
    _is_causal_query,
    _point,
    _summary_clue,
)


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="aml-v17-")),
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


# -- F1 summary substance -------------------------------------------------

def test_summary_point_keeps_more_substance():
    # long first line: v1.7 head is 160 chars (was 96), so more substance
    # survives truncation for the answer generator
    long_text = "我上周在福州见了一个客户，谈成了明年的合作框架，他很满意。" * 4
    point = _point(long_text)
    assert len(point) > 96
    assert len(point) <= 160
    assert "客户" in point and "合作" in point


def test_summary_trigger_still_fires():
    assert _summary_clue("给我讲讲我的故事")
    assert _summary_clue("what happened in my life")


# -- B2 causal questions --------------------------------------------------

def test_causal_query_detection():
    for q in ("她为什么搬走了", "怎么变成这样的", "后来发生了什么",
              "why did she leave", "how did this happen", "what led to the move"):
        assert _is_causal_query(q), q


def test_ordinary_question_not_causal():
    assert not _is_causal_query("她住在哪里")
    assert not _is_causal_query("where does she live")


def test_causal_query_pulls_farther_neighbors():
    svc = _service()
    # a seed far apart from the cause: a radius-3 window would miss it,
    # radius-5 (causal) reaches it.
    turns = ["我们开始谈合作", "我介绍了我们的产品", "他们问了很多细节",
             "后来价格谈不拢", "合作取消了"]
    _add(svc, "u1", "s1", turns, request_id="r1")
    res = svc.search({"query": "为什么合作取消", "user_id": "u1", "top_k": 20})
    contents = [e["content"] for e in res["data"]]
    assert any("价格" in c for c in contents)


# -- D1 contact domain ----------------------------------------------------

def test_contact_predicate_group():
    assert predicate_group("我的电话是 13800138000") == "contact"
    assert predicate_group("我的手机号换了") == "contact"
    assert predicate_group("我的邮箱是 a@b.com") == "contact"


def test_contact_restatement_supersedes():
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, NULL)",
        ("old", "我的电话是 13800138000", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    n = record_possible_updates(
        conn,
        memory_id="new",
        content="我的电话换成了 13900139000",
        event_at=None,
        session_position=1,
        created_at="2026-02-01T00:00:00+00:00",
        at="2026-03-01T00:00:00+00:00",
    )
    assert n == 1
    row = conn.execute(
        "SELECT superseded_by FROM aml_updates WHERE memory_id = 'old'"
    ).fetchone()
    assert row is not None and row[0] == "new"
    conn.close()


def _fresh_conn():
    import sqlite3

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
    return conn
