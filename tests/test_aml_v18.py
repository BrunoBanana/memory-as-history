"""Tests for v1.8 A1 synonym query expansion.

Single-fact questions use words the stored memory does not ("手机号" vs
"电话", "家" vs "住在", "name" vs "called"). Expansion appends synonyms to
the retrieval input only; triggers (summary/rule/temporal) keep the
original query, and expansion never fires without a dictionary hit.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory_as_history.aml import MemoryService
from memory_as_history.aml.memory_service import _expand_query, _summary_clue


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="aml-v18-")),
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


# -- expansion -----------------------------------------------------------

def test_expand_query_appends_synonyms():
    q = _expand_query("她的手机号是多少")
    assert "电话" in q
    q = _expand_query("她的家在哪里")
    assert "住在" in q
    q = _expand_query("what is her phone number")
    assert "mobile" in q or "tel" in q


def test_expand_query_unchanged_without_hit():
    assert _expand_query("今天天气怎么样") == "今天天气怎么样"
    assert _expand_query("how are you") == "how are you"


def test_expand_query_never_duplicates():
    q = _expand_query("我的电话和手机")
    assert q.count("联系方式") == 1


# -- retrieval -----------------------------------------------------------

def test_synonym_query_finds_stored_phone():
    svc = _service()
    _add(svc, "u1", "s1", ["我的电话号码是 13800138000。"], request_id="r1")
    res = svc.search({"query": "她的手机号是多少", "user_id": "u1", "top_k": 10})
    assert any("13800138000" in e["content"] for e in res["data"])


def test_synonym_query_finds_stored_home():
    svc = _service()
    _add(svc, "u1", "s1", ["我住在福州，后来搬到上海。"], request_id="r1")
    res = svc.search({"query": "她的家在哪里", "user_id": "u1", "top_k": 10})
    assert any("福州" in e["content"] for e in res["data"])


def test_english_synonym_query():
    svc = _service()
    _add(svc, "u1", "s1", ["My phone number is 13800138000."], request_id="r1")
    res = svc.search({"query": "What is her mobile number?", "user_id": "u1", "top_k": 10})
    assert any("13800138000" in e["content"] for e in res["data"])


def test_expansion_does_not_pollute_summary_trigger():
    # expansion must not change trigger decisions: summary trigger still
    # reads the original query
    assert not _summary_clue(_expand_query("她的手机号是多少"))
    assert _summary_clue(_expand_query("给我讲讲我的故事"))


def test_contract_clean_after_expansion():
    svc = _service()
    _add(svc, "u1", "s1", ["我的电话号码是 13800138000。"], request_id="r1")
    res = svc.search({"query": "她的手机号是多少", "user_id": "u1", "top_k": 10})
    for item in res["data"]:
        assert set(item) <= {"id", "content", "score", "created_at"}
