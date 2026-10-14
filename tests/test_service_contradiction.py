"""Tests for v1.5 D2 contradiction detection & conflict resolution.

Two statements about the same subject and fact domain with flipped
polarity ("她喜欢猫" / "她讨厌猫") are contradictory — both sides are
preserved (history intact), the pair is recorded at write time, and at read
time retrieving either side pulls in the other, so the answer generator
sees the conflict in full instead of one-sided evidence.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from memory_as_history.service import MemoryService
from memory_as_history.service.governance import (
    contradiction_signal,
    record_possible_contradictions,
)


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="mem-con-")),
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

def test_contradiction_signal_negation_flip():
    assert contradiction_signal("她喜欢猫", "她讨厌猫") is not None
    assert contradiction_signal("她住在福州", "她不住在福州") == "negation_flip"
    assert contradiction_signal("李明支持这个方案", "李明反对这个方案") is not None


def test_contradiction_signal_requires_same_domain():
    # different domains (residence vs preference): independent facts
    assert contradiction_signal("她住在福州", "她喜欢画画") is None


def test_contradiction_signal_requires_shared_subject():
    assert contradiction_signal("她喜欢猫", "他讨厌猫") is None


def test_contradiction_signal_no_false_positive_on_negation_both_sides():
    assert contradiction_signal("她不喜欢猫", "他不喜欢猫") is None


# -- write-time recording ------------------------------------------------

def test_contradiction_recorded_and_audited():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT, event_at TEXT,
            session_position INTEGER, created_at TEXT, forgotten_at TEXT
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
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, NULL)",
        ("old", "她喜欢猫", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    count = record_possible_contradictions(
        conn,
        memory_id="new",
        content="她讨厌猫",
        event_at=None,
        session_position=1,
        created_at="2026-02-01T00:00:00+00:00",
        at="2026-03-01T00:00:00+00:00",
    )
    assert count == 1
    # both directions stored for a symmetric single-query lookup
    pairs = conn.execute(
        "SELECT memory_id, conflicts_with FROM memory_contradictions"
    ).fetchall()
    assert len(pairs) == 2
    assert {p["memory_id"] for p in pairs} == {"old", "new"}
    audit = conn.execute(
        "SELECT action FROM audit_log WHERE action = 'contradiction'"
    ).fetchone()
    assert audit is not None
    conn.close()


# -- read-time partner recall --------------------------------------------

def test_contradiction_partner_surfaced_in_search():
    svc = _service()
    _add(svc, "u1", "s1", ["她喜欢猫"], request_id="r1")
    _add(svc, "u1", "s2", ["她讨厌猫"], request_id="r2")
    # retrieving one side of the pair must pull in the other side
    res = svc.search({"query": "她喜欢猫", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert "她喜欢猫" in contents
    assert "她讨厌猫" in contents
    # partner evidence must not outrank the ranked seed
    assert res["data"][0]["content"] == "她喜欢猫"


def test_contradiction_partner_not_pulled_without_match():
    svc = _service()
    _add(svc, "u1", "s1", ["她喜欢猫"], request_id="r1")
    _add(svc, "u1", "s2", ["她讨厌猫"], request_id="r2")
    _add(svc, "u1", "s3", ["她住在杭州"], request_id="r3")
    # a query about an unrelated memory must not surface the pair
    res = svc.search({"query": "她住在哪里", "user_id": "u1", "top_k": 10})
    assert any("杭州" in e["content"] for e in res["data"])
    assert not any("猫" in e["content"] for e in res["data"])


def test_contradiction_keeps_contract_clean():
    svc = _service()
    _add(svc, "u1", "s1", ["她喜欢猫"], request_id="r1")
    _add(svc, "u1", "s2", ["她讨厌猫"], request_id="r2")
    res = svc.search({"query": "猫", "user_id": "u1", "top_k": 10})
    for item in res["data"]:
        assert set(item) <= {"id", "content", "score", "created_at"}
