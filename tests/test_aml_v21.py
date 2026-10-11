"""Tests for v2.1 G2 rule-QA boost + B2 causal trigger expansion.

- "她可以吃辣吗 / can she eat spicy food" lifts the stored rule
  ("她不能吃辣 / she never eats spicy") above merely-similar memories.
- Causal triggers grow: 造成/因此/前因后果/what triggered/effect of all
  widen the neighbor window.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory_as_history.aml import MemoryService
from memory_as_history.aml.memory_service import (
    _is_causal_query,
    _rule_qa_boost,
)


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="aml-v21-")),
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


# -- G2 rule-QA ----------------------------------------------------------

def test_rule_qa_boost_detects_pattern():
    boosted = _rule_qa_boost(
        "她可以吃辣吗",
        [
            {"id": "a", "content": "她不能吃辣。", "score": 0.4},
            {"id": "b", "content": "她喜欢吃米饭。", "score": 0.6},
        ],
    )
    by_id = {e["id"]: e for e in boosted}
    assert by_id["a"]["score"] == 0.9  # rule lifted above
    assert by_id["b"]["score"] == 0.6  # non-rule untouched


def test_rule_qa_boost_english():
    boosted = _rule_qa_boost(
        "can she eat spicy food?",
        [
            {"id": "a", "content": "She never eats spicy food.", "score": 0.3},
            {"id": "b", "content": "She likes rice.", "score": 0.7},
        ],
    )
    by_id = {e["id"]: e for e in boosted}
    assert by_id["a"]["score"] == 0.9
    assert by_id["b"]["score"] == 0.7


def test_rule_qa_boost_not_for_plain_question():
    boosted = _rule_qa_boost(
        "她平时吃什么",
        [{"id": "a", "content": "她不能吃辣。", "score": 0.4}],
    )
    assert boosted[0]["score"] == 0.4


def test_rule_qa_at_search_ranks_rule_first():
    svc = _service()
    _add(svc, "u1", "s1",
         ["她喜欢吃米饭。", "她不能吃辣。"],
         request_id="r1")
    res = svc.search({"query": "她可以吃辣吗", "user_id": "u1", "top_k": 10})
    contents = [e["content"] for e in res["data"]]
    assert any("不能吃辣" in c for c in contents)
    assert contents[0].startswith("她不能吃辣")


# -- B2 causal triggers --------------------------------------------------

def test_new_causal_triggers():
    for q in ("是什么造成她搬家的", "这个决定的后果是什么",
              "事情的来龙去脉", "what triggered the move",
              "the effect of that decision", "why did she leave"):
        assert _is_causal_query(q), q


def test_causal_still_widens_window():
    svc = _service()
    _add(svc, "u1", "s1",
         ["我们开始谈合作", "我介绍了产品", "他们问了细节",
          "后来价格谈不拢", "合作取消了"],
         request_id="r1")
    res = svc.search({"query": "是什么造成合作取消", "user_id": "u1", "top_k": 20})
    contents = [e["content"] for e in res["data"]]
    assert any("价格" in c for c in contents)


def test_plain_question_not_causal():
    assert not _is_causal_query("她住在哪里")
    assert not _is_causal_query("what is her phone number")
