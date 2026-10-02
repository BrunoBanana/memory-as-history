"""Tests for AML temporal-clue reranking (P2, memory-as-history).

The project treats memory as history: a question that names a time
("where did she live LAST YEAR?") should surface the facts as they
stood then, not today's truth. Detection is conservative (explicit
temporal expressions only); multipliers are small so a misdetected
clue cannot flip a ranking.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memory_as_history.aml import MemoryService
from memory_as_history.aml.temporal import (
    detect_temporal_clues,
    parse_iso_ts,
    time_weight,
)

DAY = 86400


# -- detection -----------------------------------------------------------

def test_chinese_relative_expressions_detected():
    clues = detect_temporal_clues("她去年住在哪里")
    assert [c.label for c in clues] == ["last_year"]
    assert clues[0].offset_seconds == pytest.approx(365 * DAY)
    assert clues[0].half_window_seconds == pytest.approx(15 * DAY)


def test_chinese_week_and_month():
    assert [c.label for c in detect_temporal_clues("上周他去过哪")] == ["last_week"]
    assert [c.label for c in detect_temporal_clues("上个月她说住浦东")] == ["last_month"]


def test_yesterday_and_few_days_ago():
    assert [c.label for c in detect_temporal_clues("昨天发生了什么事")] == ["yesterday"]
    assert [c.label for c in detect_temporal_clues("几天前我们一起去的")] == ["few_days_ago"]


def test_english_expressions_detected():
    assert [c.label for c in detect_temporal_clues("where did she live last year?")] == ["last_year"]
    assert [c.label for c in detect_temporal_clues("what happened a week ago")] == ["last_week"]


def test_fuzzy_words_do_not_fire():
    # "最近/当时/recently" are ordinary conversational words; firing on
    # them would poison most queries.
    assert detect_temporal_clues("她最近怎么样") == []
    assert detect_temporal_clues("当时我们在讨论什么") == []
    assert detect_temporal_clues("have you seen her recently?") == []


def test_no_false_positive_on_plain_query():
    assert detect_temporal_clues("陈静现在居住在哪里") == []
    assert detect_temporal_clues("王磊养了什么宠物") == []


def test_overlapping_spans_claimed_once():
    clues = detect_temporal_clues("上上周和上周她都去了公园")
    labels = [c.label for c in clues]
    # both matches detected, neither raw span overlaps
    assert "last_week" in labels


def test_empty_and_non_string():
    assert detect_temporal_clues("") == []
    assert detect_temporal_clues(None) == []
    assert detect_temporal_clues("   ") == []


# -- time_weight ---------------------------------------------------------

def test_inside_window_boosted_outside_discounted():
    clue = detect_temporal_clues("她去年住在哪里")[0]
    now = 1_800_000_000.0
    inside = now - 365 * DAY  # exactly last year
    outside = now - 10 * DAY  # ten days ago
    assert time_weight(inside, now, clue) == pytest.approx(1.25)
    assert time_weight(outside, now, clue) == pytest.approx(0.8)


def test_custom_multipliers():
    clue = detect_temporal_clues("上周他去过哪")[0]
    now = 1_800_000_000.0
    assert time_weight(now - 7 * DAY, now, clue, 2.0, 0.5) == pytest.approx(2.0)
    assert time_weight(now - 100 * DAY, now, clue, 2.0, 0.5) == pytest.approx(0.5)


# -- parse_iso_ts --------------------------------------------------------

def test_parse_iso_timestamps():
    assert parse_iso_ts("2026-10-01T00:00:00+00:00") == pytest.approx(1_790_812_800.0)
    assert parse_iso_ts("2026-10-01T00:00:00Z") == pytest.approx(1_790_812_800.0)
    assert parse_iso_ts(1_790_812_800.0) == pytest.approx(1_790_812_800.0)
    assert parse_iso_ts(None) is None
    assert parse_iso_ts("not-a-date") is None


# -- integration through MemoryService.search ----------------------------

class FakeStore:
    """Stand-in for Store; rows carry session fields so neighbor expansion
    is skipped (no _conn) and temporal rerank can be observed directly."""

    _conn = None

    def __init__(self, rows):
        self.rows = rows

    def recall(self, query, limit=10):
        return {"anchors": [], "canon": [], "memories": self.rows}

    def search(self, query, limit=10, mode="hybrid"):
        return {"anchors": [], "canon": [], "memories": self.rows}

    def close(self):
        pass


def _service(monkeypatch, rows, use_temporal=True, mode="lexical"):
    data_dir = Path(tempfile.mkdtemp(prefix="aml-tmp-"))
    service = MemoryService(
        data_dir=data_dir, search_mode=mode, use_temporal=use_temporal
    )
    store = FakeStore(rows)
    monkeypatch.setattr(service, "_user_store", lambda user_id: store)
    return service


def _row(rid, content, relevance, created_at):
    return {
        "id": rid,
        "content": content,
        "relevance": relevance,
        "semantic_similarity": 0.0,
        "search_score": 0.0,
        "created_at": created_at,
    }


def test_temporal_query_boosts_in_window_row(monkeypatch):
    now = 1_800_000_000.0  # arbitrary epoch "today"
    rows = [
        _row("a", "她去年住在杭州", 5.0, _iso(now - 370 * DAY)),  # inside last-year window
        _row("b", "她最近搬到了上海", 5.0, _iso(now - 2 * DAY)),  # outside, newer
    ]
    svc = _service(monkeypatch, rows)
    res = svc.search({"query": "她去年住在哪里", "user_id": "u1", "top_k": 10})
    scores = {r["id"]: r["score"] for r in res["data"]}
    # in-window row boosted above its raw relevance, newer row discounted
    assert scores["a"] > scores["b"]
    assert scores["a"] == pytest.approx(5.0 * 1.25)
    assert scores["b"] == pytest.approx(5.0 * 0.8)


def test_plain_query_unaffected(monkeypatch):
    now = 1_800_000_000.0
    rows = [
        _row("a", "她去年住在杭州", 5.0, _iso(now - 370 * DAY)),
        _row("b", "她最近搬到了上海", 5.0, _iso(now - 2 * DAY)),
    ]
    svc = _service(monkeypatch, rows)
    res = svc.search({"query": "她现在住在哪里", "user_id": "u1", "top_k": 10})
    scores = {r["id"]: r["score"] for r in res["data"]}
    assert scores["a"] == pytest.approx(5.0)
    assert scores["b"] == pytest.approx(5.0)


def test_temporal_disabled_leaves_scores_untouched(monkeypatch):
    now = 1_800_000_000.0
    rows = [
        _row("a", "她去年住在杭州", 5.0, _iso(now - 370 * DAY)),
        _row("b", "她最近搬到了上海", 5.0, _iso(now - 2 * DAY)),
    ]
    svc = _service(monkeypatch, rows, use_temporal=False)
    res = svc.search({"query": "她去年住在哪里", "user_id": "u1", "top_k": 10})
    scores = {r["id"]: r["score"] for r in res["data"]}
    assert scores["a"] == pytest.approx(5.0)
    assert scores["b"] == pytest.approx(5.0)


def _iso(epoch: float) -> str:
    import datetime as dt

    return (
        dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
