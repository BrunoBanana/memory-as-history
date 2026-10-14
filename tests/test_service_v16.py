"""Tests for v1.6 capability enhancements.

- C1 time-context: years named in content ("2021年我住在福州") are indexed
  at write time; a question anchored to a year boosts the memories that
  describe that year, so the archive answers "where did she live in 2021?"
  with the fact as it stood then.
- F1 trigger expansion: story / 一生 / 过往 / "what happened" / "my life"
  surface the global summary.
- G2 rule memory expansion: English rule statements and rule questions
  ("always", "never", "allowed", "can i") are recognized.
- C1 relative words: 大前天 / 上上个月 / 上周五 / 上上周 fire.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory_as_history.service import MemoryService
from memory_as_history.service.memory_service import (
    _content_years,
    _is_rule_question,
    _is_rule_statement,
    _summary_clue,
)
from memory_as_history.service.temporal import detect_temporal_clues


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="mem-v16-")),
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


# -- C1 content years ----------------------------------------------------

def test_content_year_extraction():
    assert _content_years("2021年我住在福州") == [2021]
    assert _content_years("in 2020 we moved to Shanghai") == [2020]
    assert _content_years("during 2019 I studied abroad") == [2019]
    assert _content_years("我住在福州") == []
    assert _content_years("那是2009年的事") == [2009]


def test_query_years_from_clues():
    clues = detect_temporal_clues("where did she live in 2021")
    labels = {c.label for c in clues}
    assert "year_in" in labels
    clues2 = detect_temporal_clues("她2021年住在哪里")
    assert any(c.label in ("year_in", "month_year") for c in clues2)


def test_year_anchored_question_boosts_content_year():
    svc = _service()
    _add(svc, "u1", "s1", ["2021年我住在福州，后来搬到上海。"], request_id="r1")
    _add(svc, "u1", "s2", ["我今年搬到了北京。"], request_id="r2")
    res = svc.search({"query": "2021年她住在哪里", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert any("福州" in c for c in contents)
    # the year-matched memory must rank first among scored entries
    assert res["data"][0]["content"].startswith("2021年我住在福州")


def test_non_year_question_unaffected():
    svc = _service()
    _add(svc, "u1", "s1", ["2021年我住在福州，后来搬到上海。"], request_id="r1")
    _add(svc, "u1", "s2", ["我今年搬到了北京。"], request_id="r2")
    res = svc.search({"query": "搬到了北京", "user_id": "u1", "top_k": 10})
    # no year anchor -> no time-context boost; current-state governs
    assert any("北京" in e["content"] for e in res["data"])


# -- C1 relative words ---------------------------------------------------

def test_new_relative_time_words():
    clues = detect_temporal_clues("大前天她来我家")
    assert any(c.label == "three_days_ago" for c in clues)
    clues = detect_temporal_clues("上上个月我们去了日本")
    assert any(c.label == "two_months_ago" for c in clues)
    clues = detect_temporal_clues("上周五开会讨论过")
    assert any(c.label == "last_weekday" for c in clues)
    clues = detect_temporal_clues("上上周见过他")
    assert any(c.label == "two_weeks_ago" for c in clues)


# -- F1 summary triggers -------------------------------------------------

def test_summary_trigger_expansion():
    for q in ("给我讲讲我的故事", "我的一生都发生了什么", "回顾我的过往",
              "what happened in my life", "tell me my backstory"):
        assert _summary_clue(q), q


def test_ordinary_query_no_summary_trigger():
    assert not _summary_clue("我喜欢吃什么")
    assert not _summary_clue("where did I put my keys")


# -- G2 rule expansion ---------------------------------------------------

def test_english_rule_statement_detected():
    for s in ("I always take the stairs.", "Never call after 10pm.",
              "You must finish homework first.", "No smoking in the house."):
        assert _is_rule_statement(s), s


def test_english_rule_question_detected():
    for q in ("What are the house rules?", "Am I allowed to eat in the room?",
              "What are the requirements?", "Can I bring a guest?"):
        assert _is_rule_question(q), q


def test_chinese_rule_expansion():
    assert _is_rule_statement("睡前一定要关灯")
    assert _is_rule_statement("吃饭不许玩手机")
    assert _is_rule_question("有什么要注意的")
    assert _is_rule_question("有什么讲究吗")


def test_rule_question_surfaces_english_rule():
    svc = _service()
    _add(svc, "u1", "s1", ["We never smoke inside the house."], request_id="r1")
    res = svc.search({"query": "What are the house rules?", "user_id": "u1", "top_k": 10})
    assert any("smoke" in e["content"] for e in res["data"])
