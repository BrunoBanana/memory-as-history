"""Tests for v2.4: structured-triple version governance on the REAL
official corpus shapes.

The official evaluation injects knowledge as JSON triples with stable
identifiers, and revises facts through an explicit instruction:

  Corpus: UPDATE: replace the prior value of this subject and relation
  with the following fact.
  {"subject": "Hyderabad", "relation": "continent", "object": "Eurasia",
   "subject_id": "Q1361", "relation_id": "P30", "object_id": "Q540"}

v2.4 adds:
- ``_triple_version``: same subject_id + relation_id (or, without ids, same
  subject + relation) is a version of the same fact — no similarity gate.
- ``_version_decision`` now honors ``coverage``: a 0.52-Jaccard /
  0.85-coverage rewrite was previously rejected before the record gate
  (the parameter was accepted but never used).

D1 behavior verified on real server data before this version: the old
value ranked first and no aml_updates edge existed. After the fix the edge
is recorded and the NEW value ranks first.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from memory_as_history.aml import MemoryService
from memory_as_history.aml.governance import _triple_key, _triple_version, _version_decision

BASE = 1_700_000_000_000

TRIPLE_ASIA = (
    'Corpus: {"subject": "Hyderabad", "relation": "continent", "object": "Asia", '
    '"subject_id": "Q1361", "relation_id": "P30", "object_id": "Q48"}'
)
TRIPLE_EURASIA = (
    "Corpus: UPDATE: replace the prior value of this subject and relation with "
    "the following fact.\n"
    '{"subject": "Hyderabad", "relation": "continent", "object": "Eurasia", '
    '"subject_id": "Q1361", "relation_id": "P30", "object_id": "Q540"}'
)
TRIPLE_DIFFERENT = (
    'Corpus: {"subject": "Beijing", "relation": "continent", "object": "Asia", '
    '"subject_id": "Q956", "relation_id": "P30", "object_id": "Q48"}'
)


def _service():
    return MemoryService(
        data_dir=Path(tempfile.mkdtemp(prefix="aml-v24-")),
        search_mode="lexical",
    )


def _add(svc, user_id, session_id, texts, request_id, base_ts=BASE):
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


def _updates(svc, user_id) -> list[tuple[str, str]]:
    import hashlib

    db = Path(svc.data_dir) / "users" / (
        hashlib.sha256(user_id.encode()).hexdigest()[:32] + ".db"
    )
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT memory_id, superseded_by FROM aml_updates"
    ).fetchall()
    conn.close()
    return rows


# -- key extraction -------------------------------------------------------

def test_triple_key_uses_stable_ids():
    assert _triple_key(TRIPLE_ASIA) == ("q1361", "p30")
    assert _triple_key(TRIPLE_EURASIA) == ("q1361", "p30")


def test_triple_key_falls_back_to_subject_relation():
    no_ids = 'Corpus: {"subject": "England", "relation": "continent", "object": "Europe"}'
    assert _triple_key(no_ids) == ("england", "continent")


def test_triple_key_none_for_plain_text():
    assert _triple_key("She moved to Shanghai.") is None
    assert _triple_key("") is None


def test_triple_version_same_ids():
    assert _triple_version(TRIPLE_ASIA, TRIPLE_EURASIA) is True


def test_triple_version_different_ids():
    assert _triple_version(TRIPLE_ASIA, TRIPLE_DIFFERENT) is False


# -- write-time version edges ---------------------------------------------

def test_update_instruction_creates_version_edge():
    svc = _service()
    _add(svc, "u1", "s1", [TRIPLE_ASIA], request_id="r1")
    _add(svc, "u1", "s1", [TRIPLE_EURASIA], request_id="r2", base_ts=BASE + 10_000)
    edges = _updates(svc, "u1")
    assert len(edges) == 1, edges


def test_different_triple_not_a_version():
    svc = _service()
    _add(svc, "u1", "s1", [TRIPLE_ASIA], request_id="r1")
    _add(svc, "u1", "s1", [TRIPLE_DIFFERENT], request_id="r2", base_ts=BASE + 10_000)
    assert _updates(svc, "u1") == []


# -- read-time ranking ----------------------------------------------------

def test_update_new_value_ranks_first():
    svc = _service()
    _add(svc, "u1", "s1", [TRIPLE_ASIA], request_id="r1")
    _add(svc, "u1", "s1", [TRIPLE_EURASIA], request_id="r2", base_ts=BASE + 10_000)
    res = svc.search(
        {"query": "What is the continent of Hyderabad?", "user_id": "u1", "top_k": 10}
    )
    data = res["data"]
    assert data, "no evidence returned"
    assert "Eurasia" in data[0]["content"], data[0]["content"]
    assert "Asia" in data[-1]["content"]


def test_current_value_search_includes_new_object():
    svc = _service()
    _add(svc, "u1", "s1", [TRIPLE_ASIA], request_id="r1")
    _add(svc, "u1", "s1", [TRIPLE_EURASIA], request_id="r2", base_ts=BASE + 10_000)
    res = svc.search(
        {"query": "What is the continent of Hyderabad?", "user_id": "u1", "top_k": 10}
    )
    joined = " ".join(e["content"] for e in res["data"])
    assert "Eurasia" in joined


# -- coverage bug fix (no triple shape) -----------------------------------

def test_plain_text_longer_rewrite_is_a_version():
    """A longer plain-text rewrite that fully contains the old statement is
    a version via the length+subset path (no triple shape needed)."""
    old = "She lives in Paris with her dog named Max."
    new = "She lives in Paris with her dog named Max and a cat named Lily."
    assert _version_decision(0.52, 0.85, old, new) is True

    svc = _service()
    _add(svc, "u1", "s1", [old], request_id="r1")
    _add(svc, "u1", "s1", [new], request_id="r2", base_ts=BASE + 10_000)
    assert len(_updates(svc, "u1")) == 1


def test_same_length_near_duplicates_are_not_versions():
    # Numbered/templated memories (same length, only the ordinal differs)
    # are distinct facts, not versions — even with identical token sets.
    old_text, new_text = "记忆内容第8条，包含关键词芒果", "记忆内容第9条，包含关键词芒果"
    assert _version_decision(1.0, 1.0, old_text, new_text) is False


# -- A1 retrieval on official shapes (English) ----------------------------

def test_kg_triple_retrieval_en():
    svc = _service()
    _add(svc, "u1", "s1", [TRIPLE_ASIA], request_id="r1")
    res = svc.search(
        {"query": "What is the continent of Hyderabad?", "user_id": "u1", "top_k": 10}
    )
    assert res["data"]
    assert "Asia" in res["data"][0]["content"]


def test_wiki_passage_retrieval_en():
    svc = _service()
    passage = (
        "Corpus: Passage ID: /wiki/Harvard_University\n"
        "Harvard University is a private Ivy League research university "
        "in Cambridge, Massachusetts."
    )
    _add(svc, "u1", "s1", [passage], request_id="r1")
    res = svc.search(
        {"query": "Tell me about Harvard University", "user_id": "u1", "top_k": 10}
    )
    assert res["data"]
    assert "Harvard" in res["data"][0]["content"]
