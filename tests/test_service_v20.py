"""Tests for v2.0 I1 revocation + D1 age domain.

- "我不喜欢咖啡了" revokes "我喜欢咖啡": it must be a VERSION (old value
  discounted, current state wins), NOT a contradiction pair kept alive.
- "我今年25岁" -> "我今年26岁" is a same-domain (identity/age) version.
- Genuine stable conflicts ("她喜欢猫" / "她讨厌猫") must still be
  contradictions — revocation must not swallow them.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from memory_as_history.service import MemoryService
from memory_as_history.service.governance import (
    _is_revocation,
    contradiction_signal,
    predicate_group,
    record_possible_updates,
)

DEFAULT_TS = 1_700_000_000_000


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="mem-v20-")),
        search_mode="lexical",
    )


def _add(svc, user_id, session_id, texts, request_id="r1", base_ts=DEFAULT_TS):
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


# -- revocation detection -------------------------------------------------

def test_revocation_detection():
    assert _is_revocation("我不喜欢咖啡了")
    assert _is_revocation("他不再支持这个方案")
    assert _is_revocation("我没有养猫了")
    assert _is_revocation("我没有养狗了")


def test_plain_negation_not_revocation():
    assert not _is_revocation("今天不下雨")
    assert not _is_revocation("她不在这里")
    assert not _is_revocation("这本书不好看")  # no known fact domain
    assert not _is_revocation("从不吃辣")  # no domain predicate


def test_stable_negative_preference_not_revocation():
    # "讨厌猫" is a stable preference state, not a revocation: the
    # 喜欢/讨厌 contradiction must keep firing.
    assert not _is_revocation("她讨厌猫")


# -- revocation is a version, not a contradiction -------------------------

def test_revocation_suppresses_contradiction_signal():
    assert contradiction_signal("我喜欢咖啡", "我不喜欢咖啡了") is None
    assert contradiction_signal("我不喜欢咖啡了", "我喜欢咖啡") is None


def test_stable_conflict_still_contradiction():
    assert contradiction_signal("她喜欢猫", "她讨厌猫") is not None


def test_revocation_records_version_edge():
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, NULL)",
        ("old", "我喜欢咖啡", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    n = record_possible_updates(
        conn,
        memory_id="new",
        content="我不喜欢咖啡了",
        event_at=None,
        session_position=1,
        created_at="2026-02-01T00:00:00+00:00",
        at="2026-03-01T00:00:00+00:00",
    )
    assert n == 1
    row = conn.execute(
        "SELECT superseded_by FROM memory_updates WHERE memory_id = 'old'"
    ).fetchone()
    assert row is not None and row[0] == "new"
    # and NO contradiction pair for the same pair
    bad = conn.execute(
        "SELECT COUNT(*) FROM memory_contradictions "
        "WHERE memory_id IN ('old','new')"
    ).fetchone()[0]
    assert bad == 0
    conn.close()


def test_revocation_current_state_wins_at_search():
    svc = _service()
    _add(svc, "u1", "s1", ["我喜欢咖啡。"], request_id="r1")
    _add(svc, "u1", "s2", ["我不喜欢咖啡了。"], request_id="r2", base_ts=DEFAULT_TS + 10_000)
    res = svc.search({"query": "她喜欢咖啡吗", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert any("不喜欢咖啡" in c for c in contents)
    idx_new = next(i for i, c in enumerate(contents) if "不喜欢咖啡" in c)
    assert contents[idx_new] == contents[0]  # current state ranks first


# -- D1 age domain --------------------------------------------------------

def test_age_group():
    assert predicate_group("我今年25岁") == "identity"
    assert predicate_group("她多大了") == "identity"


def test_age_version_edge():
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, NULL)",
        ("old", "我今年25岁", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    n = record_possible_updates(
        conn,
        memory_id="new",
        content="我今年26岁",
        event_at=None,
        session_position=1,
        created_at="2026-06-01T00:00:00+00:00",
        at="2026-06-02T00:00:00+00:00",
    )
    assert n == 1
    row = conn.execute(
        "SELECT superseded_by FROM memory_updates WHERE memory_id = 'old'"
    ).fetchone()
    assert row is not None and row[0] == "new"
    conn.close()


def test_age_current_value_wins_at_search():
    svc = _service()
    _add(svc, "u1", "s1", ["我今年25岁。"], request_id="r1")
    _add(svc, "u1", "s2", ["我今年26岁。"], request_id="r2", base_ts=DEFAULT_TS + 10_000)
    res = svc.search({"query": "她今年多大", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert any("26岁" in c for c in contents)
    assert contents[0].startswith("我今年26岁")


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT, event_at TEXT,
            session_position INTEGER, created_at TEXT, forgotten_at TEXT
        );
        CREATE TABLE memory_updates (
            memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL,
            similarity REAL NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (memory_id, superseded_by)
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
    return conn
