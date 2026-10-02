"""Temporal clue detection for the AML layer (memory-as-history).

A memory system that treats conversation as history answers
"where did she live LAST YEAR?" with the facts as they stood at that
moment, not with today's truth. This module only *detects* the clue;
the caller decides how to weight/rerank evidence by time.

Detection is deliberately conservative: only explicit relative time
expressions (Chinese and English) trigger, so generic questions with
incidental time words ("recently", "当时") never fire. Weights live in
the caller and are small by default.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

DAY = 86400


@dataclass(frozen=True)
class TemporalClue:
    label: str
    offset_seconds: float  # how far before "now" the window centers
    half_window_seconds: float  # one-sided window half-width
    raw: str  # the matched text, for auditing


# Ordered: first match wins per scan position (longer/more specific first).
_RULES: list[tuple[re.Pattern[str], str, float, float]] = [
    (re.compile(r"上个?星期|上周|上礼拜"), "last_week", 7 * DAY, 1.5 * DAY),
    (re.compile(r"上个月|上月"), "last_month", 30 * DAY, 5 * DAY),
    (re.compile(r"去年|前年"), "last_year", 365 * DAY, 15 * DAY),
    (re.compile(r"前天"), "day_before_yesterday", 2 * DAY, 12 * 3600),
    (re.compile(r"昨天|昨日"), "yesterday", 1 * DAY, 12 * 3600),
    (re.compile(r"几天前|前几天"), "few_days_ago", 3 * DAY, 2 * DAY),
    (re.compile(r"几周前|两周前|三个星期前"), "few_weeks_ago", 14 * DAY, 4 * DAY),
    (re.compile(r"几个月前|半年前"), "few_months_ago", 120 * DAY, 30 * DAY),
    # English counterparts (official text track may localize LoCoMo-style
    # questions; keep English coverage for robustness).
    (re.compile(r"last week|previous week|a week ago"), "last_week", 7 * DAY, 1.5 * DAY),
    (re.compile(r"last month|a month ago"), "last_month", 30 * DAY, 5 * DAY),
    (re.compile(r"last year|a year ago|the previous year"), "last_year", 365 * DAY, 15 * DAY),
    (re.compile(r"yesterday"), "yesterday", 1 * DAY, 12 * 3600),
    (re.compile(r"a few days ago|few days ago"), "few_days_ago", 3 * DAY, 2 * DAY),
]


def detect_temporal_clues(query: str) -> list[TemporalClue]:
    """Return explicit temporal clues in the query, in scan order.

    Conservative: matches on clearly time-anchored expressions only.
    Fuzzy words (最近/当时/那时候/recently/ago alone) are intentionally
    NOT rules — they appear in ordinary questions and would misfire.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    found: list[TemporalClue] = []
    scanned: set[tuple[int, int]] = set()  # (start, end) spans already claimed
    for pattern, label, offset, half in _RULES:
        for m in pattern.finditer(query.lower()):
            span = m.span()
            if any(s < span[1] and e > span[0] for s, e in scanned):
                continue  # a more specific rule already claimed this span
            scanned.add(span)
            found.append(
                TemporalClue(label=label, offset_seconds=float(offset),
                             half_window_seconds=float(half), raw=m.group(0))
            )
    return found


def parse_iso_ts(value: object) -> float | None:
    """Parse the project ISO timestamp (``+00:00`` / ``Z`` form) to epoch."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def time_weight(created_at_ts: float, now_ts: float, clue: TemporalClue,
                inside_multiplier: float = 1.25,
                outside_multiplier: float = 0.8) -> float:
    """Conservative rerank multiplier.

    Memories whose timestamp falls inside the clue's window get a small
    boost; everything else a small discount. Multipliers are modest so a
    misdetected clue cannot flip ranking drastically.
    """
    target = now_ts - clue.offset_seconds
    gap = abs(created_at_ts - target)
    if gap <= clue.half_window_seconds:
        return inside_multiplier
    return outside_multiplier
