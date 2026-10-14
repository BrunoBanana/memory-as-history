"""Temporal clue detection for the memory service (memory-as-history).

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
    anchor_epoch: float | None = None  # absolute anchor (e.g. "in 2021") when known


def _year_epoch(year: int) -> float:
    return _dt.datetime(year, 1, 1, tzinfo=_dt.timezone.utc).timestamp()


def _month_epoch(month: int, day: int) -> float:
    return _dt.datetime(2000, month, day, tzinfo=_dt.timezone.utc).timestamp()


# Ordered: first match wins per scan position (longer/more specific first).
# Entries with anchor_epoch are absolute time anchors ("in 2021"); entries
# without are relative offsets ("last year"). Digit-counted relative words
# ("3 years ago") are handled separately in detect_temporal_clues.
_RULES: list[tuple[re.Pattern[str], str, float, float, float | None]] = [
    # -- Chinese relative ------------------------------------------------
    # Longer, more specific expressions first so "上周五" wins over "上周",
    # "上上个月" over "上个月", "大前天" over "前天".
    (re.compile(r"上上个月|两个月前"), "two_months_ago", 60 * DAY, 5 * DAY, None),
    (re.compile(r"上上周|上上个星期"), "two_weeks_ago", 14 * DAY, 3 * DAY, None),
    (re.compile(r"上周[一二三四五六日天]|上个星期[一二三四五六日天]"), "last_weekday", 7 * DAY, 3 * DAY, None),
    (re.compile(r"上个?星期|上周|上礼拜"), "last_week", 7 * DAY, 1.5 * DAY, None),
    (re.compile(r"上个月|上月"), "last_month", 30 * DAY, 5 * DAY, None),
    (re.compile(r"去年|前年"), "last_year", 365 * DAY, 15 * DAY, None),
    (re.compile(r"大前天"), "three_days_ago", 3 * DAY, 12 * 3600, None),
    (re.compile(r"前天"), "day_before_yesterday", 2 * DAY, 12 * 3600, None),
    (re.compile(r"昨天|昨日"), "yesterday", 1 * DAY, 12 * 3600, None),
    (re.compile(r"几天前|前几天"), "few_days_ago", 3 * DAY, 2 * DAY, None),
    (re.compile(r"几周前|两周前|三个星期前"), "few_weeks_ago", 14 * DAY, 4 * DAY, None),
    (re.compile(r"几个月前|半年前"), "few_months_ago", 120 * DAY, 30 * DAY, None),
    # -- English relative ------------------------------------------------
    (re.compile(r"last week|previous week|the week before last|a week ago"),
     "last_week", 7 * DAY, 1.5 * DAY, None),
    (re.compile(r"last month|a month ago"), "last_month", 30 * DAY, 5 * DAY, None),
    (re.compile(r"last year|a year ago|the previous year"),
     "last_year", 365 * DAY, 15 * DAY, None),
    (re.compile(r"yesterday|last night"), "yesterday", 1 * DAY, 12 * 3600, None),
    (re.compile(r"a couple of days ago|the last few days|the past few days"),
     "few_days_ago", 3 * DAY, 2 * DAY, None),
    (re.compile(r"a few days ago|few days ago"), "few_days_ago", 3 * DAY, 2 * DAY, None),
    (re.compile(r"the last few weeks|the past few weeks|a couple of weeks ago"),
     "few_weeks_ago", 14 * DAY, 4 * DAY, None),
    (re.compile(r"the last few months|the past few months|a couple of months ago"),
     "few_months_ago", 120 * DAY, 30 * DAY, None),
    (re.compile(r"earlier this week"), "earlier_week", 3 * DAY, 1 * DAY, None),
    (re.compile(r"earlier this month"), "earlier_month", 15 * DAY, 5 * DAY, None),
    (re.compile(r"earlier this year"), "earlier_year", 180 * DAY, 30 * DAY, None),
    (re.compile(r"last spring|last summer|last autumn|last fall|last winter"),
     "last_season", 180 * DAY, 60 * DAY, None),
    # -- Absolute anchors (dates the question names) ---------------------
    (re.compile(r"from (?:the year )?(20\d\d) (?:to|until|through|till) (?:the year )?(20\d\d)"),
     "year_range", 0.0, 0.0, None),  # handled by anchor logic below
    (re.compile(r"between (?:the years? )?(20\d\d) and (?:the year )?(20\d\d)"),
     "year_range", 0.0, 0.0, None),
    (re.compile(r"since (?:the year )?(20\d\d)"), "year_since", 0.0, 0.0, None),
    (re.compile(r"after (?:the year )?(20\d\d)"), "year_after", 0.0, 0.0, None),
    (re.compile(r"in (?:the year )?(20\d\d)"), "year_in", 0.0, 0.0, None),
]


# Absolute month-date anchor: "on March 5", "on the 3rd of May". Year not
# given, so it anchors to "this year" (offset from now), which is the only
# safe interpretation without a year.
_DATE_PATTERN = re.compile(
    r"on (?:the )?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* "
    r"(\d{1,2})(?:st|nd|rd|th)?"
)
# Digit-counted relative time: "3 days ago", "2 weeks ago", "five years ago".
# Bare "a/an" is deliberately excluded — "a week ago" stays with the fixed
# table (standard last_week window); "a couple of" / "a few" are counted.
_DIGIT_AGO_PATTERN = re.compile(
    r"(\d+|two|three|four|five|six|seven|eight|nine|ten|"
    r"a couple of|a few|several)\s+"
    r"(day|week|month|year)s?\s+ago"
)
_WORD_NUM = {"two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "a couple of": 2, "a few": 3, "several": 5}
_UNIT_SECONDS = {"day": DAY, "week": 7 * DAY, "month": 30 * DAY, "year": 365 * DAY}

# -- v1.5: Chinese digit-counted relative time ---------------------------
# "三年前" / "两个月前" / "五天前". Bare 几/半 intentionally stay in the
# fixed table (few_*) so existing behavior does not regress; only explicit
# numerals fire here.
_CN_DIGIT_AGO_PATTERN = re.compile(
    r"(一|二|两|三|四|五|六|七|八|九|十|[0-9]+)\s*(天|周|星期|个月|月|年)前"
)
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CN_UNIT_SECONDS = {"天": DAY, "周": 7 * DAY, "星期": 7 * DAY,
                    "个月": 30 * DAY, "月": 30 * DAY, "年": 365 * DAY}

# -- v1.5: Chinese absolute years / month-year ---------------------------
_CN_MONTH_YEAR = re.compile(r"(20\d\d)年(1[0-2]|[1-9])月")
_CN_YEAR_RANGE = re.compile(r"(?:从|自)?(20\d\d)年\s*(?:到|至)\s*(20\d\d)年")
_CN_YEAR_SINCE = re.compile(r"(20\d\d)年以来")
_CN_YEAR_AFTER = re.compile(r"(20\d\d)年以后")
_CN_YEAR_IN = re.compile(r"(20\d\d)年")

# -- v1.5: English "during 2020", "March 2020", "in March", "on March 5, 2020"
_DURING_YEAR = re.compile(r"during (?:the year )?(20\d\d)")
_EN_MONTH_YEAR = re.compile(
    r"(?:in |during )?(?:the month of )?"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(20\d\d)"
)
_EN_MONTH = re.compile(r"in (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*")
_EN_DATE_YEAR = re.compile(
    r"on (?:the )?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* "
    r"(\d{1,2})(?:st|nd|rd|th)?,?\s*(20\d\d)"
)
_MONTH_NUM = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _month_year_epoch(year: int, month: int) -> float:
    return _dt.datetime(year, month, 1, tzinfo=_dt.timezone.utc).timestamp()


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
    lowered = query.lower()

    def claim(span: tuple[int, int]) -> bool:
        if any(s < span[1] and e > span[0] for s, e in scanned):
            return False
        scanned.add(span)
        return True

    # Digit-counted relative words first: they are explicit and specific.
    for m in _DIGIT_AGO_PATTERN.finditer(lowered):
        if not claim(m.span()):
            continue
        raw_num = m.group(1).strip()
        unit = m.group(2)
        n = _WORD_NUM.get(raw_num) if raw_num in _WORD_NUM else None
        if n is None:
            try:
                n = int(raw_num)
            except ValueError:
                continue
        unit_s = _UNIT_SECONDS[unit]
        found.append(TemporalClue(
            label=f"{n}_{unit}s_ago",
            offset_seconds=float(n * unit_s),
            half_window_seconds=float(unit_s / 2),
            raw=m.group(0),
        ))

    # Chinese digit-counted relative time ("三年前", "两个月前").
    for m in _CN_DIGIT_AGO_PATTERN.finditer(query):
        if not claim(m.span()):
            continue
        raw_num = m.group(1).strip()
        unit = m.group(2)
        n = _CN_NUM.get(raw_num) if raw_num in _CN_NUM else None
        if n is None:
            try:
                n = int(raw_num)
            except ValueError:
                continue
        unit_s = _CN_UNIT_SECONDS[unit]
        unit_label = {"天": "days", "周": "weeks", "星期": "weeks",
                      "个月": "months", "月": "months", "年": "years"}[unit]
        found.append(TemporalClue(
            label=f"{n}_{unit_label}_ago",
            offset_seconds=float(n * unit_s),
            half_window_seconds=float(unit_s / 2),
            raw=m.group(0),
        ))

    for pattern, label, offset, half, _anchor in _RULES:
        for m in pattern.finditer(lowered):
            if not claim(m.span()):
                continue
            if label == "year_range":
                years = [int(g) for g in m.groups() if g]
                years = [y for y in years if 1900 <= y <= 2100]
                if not years:
                    continue
                start, end = min(years), max(years)
                center = _year_epoch((start + end) // 2)
                found.append(TemporalClue(
                    label="year_range",
                    offset_seconds=0.0,
                    half_window_seconds=float(((end - start) / 2 + 0.5) * 365 * DAY),
                    raw=m.group(0),
                    anchor_epoch=center,
                ))
                continue
            if label in ("year_since", "year_after", "year_in"):
                year = int(m.group(1))
                if not (1900 <= year <= 2100):
                    continue
                found.append(TemporalClue(
                    label=label,
                    offset_seconds=0.0,
                    half_window_seconds=float(180 * DAY),
                    raw=m.group(0),
                    anchor_epoch=_year_epoch(year),
                ))
                continue
            found.append(TemporalClue(
                label=label, offset_seconds=float(offset),
                half_window_seconds=float(half), raw=m.group(0),
            ))

    # Chinese month-year ("2020年3月") — more specific than bare year.
    for m in _CN_MONTH_YEAR.finditer(query):
        if not claim(m.span()):
            continue
        year, month = int(m.group(1)), int(m.group(2))
        found.append(TemporalClue(
            label="month_year",
            offset_seconds=0.0,
            half_window_seconds=float(15 * DAY),
            raw=m.group(0),
            anchor_epoch=_month_year_epoch(year, month),
        ))

    # Chinese year range ("2018年到2022年", "从2019年到2021年").
    for m in _CN_YEAR_RANGE.finditer(query):
        if not claim(m.span()):
            continue
        start, end = int(m.group(1)), int(m.group(2))
        if not (1900 <= start <= 2100 and 1900 <= end <= 2100):
            continue
        lo, hi = min(start, end), max(start, end)
        found.append(TemporalClue(
            label="year_range",
            offset_seconds=0.0,
            half_window_seconds=float(((hi - lo) / 2 + 0.5) * 365 * DAY),
            raw=m.group(0),
            anchor_epoch=_year_epoch((lo + hi) // 2),
        ))

    # Chinese since / after / bare year.
    for pattern, label in ((_CN_YEAR_SINCE, "year_since"),
                           (_CN_YEAR_AFTER, "year_after")):
        for m in pattern.finditer(query):
            if not claim(m.span()):
                continue
            year = int(m.group(1))
            if not (1900 <= year <= 2100):
                continue
            found.append(TemporalClue(
                label=label, offset_seconds=0.0,
                half_window_seconds=float(180 * DAY),
                raw=m.group(0), anchor_epoch=_year_epoch(year),
            ))
    for m in _CN_YEAR_IN.finditer(query):
        if not claim(m.span()):
            continue
        year = int(m.group(1))
        if not (1900 <= year <= 2100):
            continue
        found.append(TemporalClue(
            label="year_in", offset_seconds=0.0,
            half_window_seconds=float(180 * DAY),
            raw=m.group(0), anchor_epoch=_year_epoch(year),
        ))

    # "on March 5, 2020" — precise year-anchored date.
    for m in _EN_DATE_YEAR.finditer(lowered):
        if not claim(m.span()):
            continue
        month = _MONTH_NUM[m.group(1)]
        day = min(int(m.group(2)), 28)
        year = int(m.group(3))
        found.append(TemporalClue(
            label="month_day",
            offset_seconds=0.0,
            half_window_seconds=float(3 * DAY),
            raw=m.group(0),
            anchor_epoch=_dt.datetime(year, month, day, tzinfo=_dt.timezone.utc).timestamp(),
        ))

    for m in _DATE_PATTERN.finditer(lowered):
        if not claim(m.span()):
            continue
        month = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                 "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}[m.group(1)]
        day = min(int(m.group(2)), 28)
        # Anchor to the nearest occurrence of that date (this year or last).
        now = _dt.datetime.now(_dt.timezone.utc)
        this_year = _dt.datetime(now.year, month, day, tzinfo=_dt.timezone.utc)
        cand = this_year.timestamp()
        if cand > now.timestamp():
            cand = _dt.datetime(now.year - 1, month, day, tzinfo=_dt.timezone.utc).timestamp()
        found.append(TemporalClue(
            label="month_day",
            offset_seconds=0.0,
            half_window_seconds=float(3 * DAY),
            raw=m.group(0),
            anchor_epoch=cand,
        ))

    # "during 2020" — absolute year anchor.
    for m in _DURING_YEAR.finditer(lowered):
        if not claim(m.span()):
            continue
        year = int(m.group(1))
        if not (1900 <= year <= 2100):
            continue
        found.append(TemporalClue(
            label="year_in", offset_seconds=0.0,
            half_window_seconds=float(180 * DAY),
            raw=m.group(0), anchor_epoch=_year_epoch(year),
        ))

    # "March 2020" / "in March 2020" — month-year anchor.
    for m in _EN_MONTH_YEAR.finditer(lowered):
        if not claim(m.span()):
            continue
        month = _MONTH_NUM[m.group(1)]
        year = int(m.group(2))
        if not (1900 <= year <= 2100):
            continue
        found.append(TemporalClue(
            label="month_year", offset_seconds=0.0,
            half_window_seconds=float(15 * DAY),
            raw=m.group(0), anchor_epoch=_month_year_epoch(year, month),
        ))

    # "in March" — no year, anchor the nearest occurrence of that month.
    for m in _EN_MONTH.finditer(lowered):
        if not claim(m.span()):
            continue
        month = _MONTH_NUM[m.group(1)]
        now = _dt.datetime.now(_dt.timezone.utc)
        this_year = _dt.datetime(now.year, month, 1, tzinfo=_dt.timezone.utc)
        cand = this_year.timestamp()
        if cand > now.timestamp():
            cand = _dt.datetime(now.year - 1, month, 1, tzinfo=_dt.timezone.utc).timestamp()
        found.append(TemporalClue(
            label="month_in", offset_seconds=0.0,
            half_window_seconds=float(15 * DAY),
            raw=m.group(0), anchor_epoch=cand,
        ))
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
    boost; everything else a small discount. Absolute anchors (year/date)
    use the anchor as the window center; relative clues use now minus the
    offset. Multipliers are modest so a misdetected clue cannot flip
    ranking drastically.
    """
    target = clue.anchor_epoch if clue.anchor_epoch is not None else now_ts - clue.offset_seconds
    gap = abs(created_at_ts - target)
    if gap <= clue.half_window_seconds:
        return inside_multiplier
    return outside_multiplier
