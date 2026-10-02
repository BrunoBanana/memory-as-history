"""Tests for AML hybrid/semantic search-mode filtering (P1).

Hybrid mode surfaces the RRF combined score as the contract score and
qualifies ordinary memories when the row carries EITHER a lexical hit
(BM25 > 0) OR a semantic hit at/above the similarity gate. Pure-semantic
rows below the gate are noise and must be dropped; lexical rows are kept
regardless of similarity (word-exact evidence is strong).

All tests mock ``Store.search`` so the filtering logic is exercised
deterministically without loading the E5 model.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memory_as_history.aml import MemoryService


class FakeStore:
    """Stand-in for Store: returns canned rows without touching SQLite."""

    _conn = None  # rows carry no session fields, so expansion is skipped

    def __init__(self, rows, mode="hybrid"):
        self.rows = rows
        self.mode = mode

    def recall(self, query, limit=10):
        return {"anchors": [], "canon": [], "memories": self.rows}

    def search(self, query, limit=10, mode="hybrid"):
        return {"anchors": [], "canon": [], "memories": self.rows}

    def close(self):
        pass


def _service(monkeypatch, rows, mode="hybrid", semantic_min=0.85):
    data_dir = Path(tempfile.mkdtemp(prefix="aml-hyb-"))
    service = MemoryService(data_dir=data_dir, search_mode=mode, semantic_min=semantic_min)
    store = FakeStore(rows, mode)
    monkeypatch.setattr(service, "_user_store", lambda user_id: store)
    return service


def _row(rid, content, relevance=0.0, sim=0.0, combined=0.0):
    return {
        "id": rid,
        "content": content,
        "relevance": relevance,
        "semantic_similarity": sim,
        "search_score": combined,
        "created_at": "2026-10-01T00:00:00Z",
    }


def _search(service, query="q", top_k=100):
    return service.search({"query": query, "user_id": "u1", "top_k": top_k})


# -- hybrid filtering -----------------------------------------------------


def test_hybrid_keeps_semantic_only_hits_above_threshold(monkeypatch):
    rows = [
        _row("m1", "她搬到了上海的新公寓", relevance=0.0, sim=0.92, combined=0.011),
    ]
    result = _search(_service(monkeypatch, rows))
    assert [i["content"] for i in result["data"]] == ["她搬到了上海的新公寓"]


def test_hybrid_drops_below_threshold_semantic_only_rows(monkeypatch):
    # E5 multilingual similarity for unrelated Chinese short text sits in the
    # 0.79-0.85 band; 0.83 must NOT qualify as evidence on its own.
    rows = [
        _row("m1", "无关的一段闲聊内容", relevance=0.0, sim=0.83, combined=0.008),
    ]
    result = _search(_service(monkeypatch, rows))
    assert result["data"] == []


def test_hybrid_keeps_lexical_hits_regardless_of_similarity(monkeypatch):
    rows = [
        _row("m1", "陈静计划搬去上海", relevance=3.4, sim=0.05, combined=0.045),
    ]
    result = _search(_service(monkeypatch, rows))
    assert [i["content"] for i in result["data"]] == ["陈静计划搬去上海"]


def test_hybrid_uses_search_score_for_contract_score(monkeypatch):
    rows = [
        _row("m1", "语义命中但词法分低", relevance=0.2, sim=0.71, combined=0.033),
    ]
    result = _search(_service(monkeypatch, rows))
    assert result["data"][0]["score"] == pytest.approx(0.033)


def test_hybrid_respects_high_gate(monkeypatch):
    rows = [
        _row("m1", "弱相关但接近", relevance=0.0, sim=0.55, combined=0.01),
        _row("m2", "强相关", relevance=0.0, sim=0.78, combined=0.02),
    ]
    result = _search(_service(monkeypatch, rows, semantic_min=0.60))
    assert [i["content"] for i in result["data"]] == ["强相关"]


def test_hybrid_zero_combined_is_dropped(monkeypatch):
    rows = [
        _row("m1", "无分", relevance=0.0, sim=0.0, combined=0.0),
    ]
    result = _search(_service(monkeypatch, rows))
    assert result["data"] == []


# -- lexical mode regression ---------------------------------------------


def test_lexical_mode_still_filters_by_relevance(monkeypatch):
    rows = [
        _row("m1", "词法命中", relevance=2.1, sim=0.9, combined=0.05),
        _row("m2", "无词法", relevance=0.0, sim=0.9, combined=0.05),
    ]
    result = _search(_service(monkeypatch, rows, mode="lexical"))
    assert [i["content"] for i in result["data"]] == ["词法命中"]
    # lexical contract score stays BM25 relevance
    assert result["data"][0]["score"] == pytest.approx(2.1)


# -- semantic mode --------------------------------------------------------


def test_semantic_mode_uses_semantic_gate(monkeypatch):
    rows = [
        _row("m1", "语义高", relevance=0.0, sim=0.90, combined=0.02),
        _row("m2", "语义低", relevance=0.0, sim=0.10, combined=0.005),
    ]
    result = _search(_service(monkeypatch, rows, mode="semantic"))
    assert [i["content"] for i in result["data"]] == ["语义高"]


# -- validation -----------------------------------------------------------


def test_semantic_min_validation():
    with pytest.raises(ValueError):
        MemoryService(search_mode="hybrid", semantic_min=1.5)
    with pytest.raises(ValueError):
        MemoryService(search_mode="hybrid", semantic_min=-0.1)
    with pytest.raises(ValueError):
        MemoryService(search_mode="hybrid", semantic_min=1)  # type: ignore[arg-type]
