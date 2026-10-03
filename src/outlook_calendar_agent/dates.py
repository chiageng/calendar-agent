"""Deterministic natural-language date resolution (Asia/Singapore by default).

Small local models are good at extracting *what* the user said ("next Tuesday 2pm") and bad at
turning it into a date. So the LLM passes the user's phrase through and this module resolves it
against the real clock. Anything genuinely ambiguous raises ``DateAmbiguity`` with the question
to ask instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from dateutil import parser as du_parser

from .timeutil import SGT

WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
_VAGUE_TIMES = {"morning", "afternoon", "evening", "night", "lunch", "lunchtime", "tonight"}
_TIME_RE = re.compile(
    r"\b(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ampm>am|pm|a\.m\.|p\.m\.)?(?!\d)",
    re.IGNORECASE,
)
_RANGE_SPLIT = re.compile(r"\s+(?:to|until|till|-|–|—|through)\s+|\s*-\s*", re.IGNORECASE)


class DateAmbiguity(Exception):
    """The phrase has more than one reasonable reading; ``question`` is what to ask the user."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime  # exclusive
    label: str


def _today(now: datetime, tz: tzinfo) -> date:
    return now.astimezone(tz).date()


def _day_window(day: date, tz: tzinfo, label: str) -> Window:
    start = datetime.combine(day, time.min, tz)
    return Window(start, start + timedelta(days=1), label)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())  # Monday


def _clean(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[,]", " ", text)
    text = re.sub(r"\b(?:12\s*)?(?:noon|midday)\b", "12:00pm", text)
    text = re.sub(r"\bmidnight\b", "12:00am", text)
    text = re.sub(r"\b(on|at|the|of|for)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---- days -----------------------------------------------------------------------------------
def resolve_day(text: str, *, now: datetime, tz: tzinfo = SGT) -> date | None:
    """Resolve a day phrase to a date, or return None if the phrase is not a day expression."""
    phrase = _clean(text)
    today = _today(now, tz)
    if phrase in {"today", "tdy"}:
        return today
    if phrase in {"tomorrow", "tmr", "tmrw", "tomorow"}:
        return today + timedelta(days=1)
    if phrase in {"day after tomorrow", "day after tmr"}:
        return today + timedelta(days=2)
    if phrase == "yesterday":
        return today - timedelta(days=1)

    m = re.fullmatch(r"(?:(this|next|coming|following)\s+)?([a-z]+)", phrase)
    if m and m.group(2) in WEEKDAYS:
        modifier, target = m.group(1), WEEKDAYS[m.group(2)]
        delta = (target - today.weekday()) % 7
        if modifier in {None, "coming"}:
            if delta == 0:
                raise DateAmbiguity(
                    f"Today is {today:%A}. Do you mean today ({today:%d %b}) or next "
                    f"{today:%A} ({today + timedelta(days=7):%d %b})?"
                )
            return today + timedelta(days=delta)
        if modifier == "this":
            candidate = _week_start(today) + timedelta(days=target)
            if candidate < today:
                raise DateAmbiguity(
                    f"This week's {candidate:%A} ({candidate:%d %b}) has passed. Do you mean "
                    f"{candidate + timedelta(days=7):%A %d %b}?"
                )
            return candidate
        # "next"/"following": the day in next calendar week (Mon-Sun)
        return _week_start(today) + timedelta(days=7 + target)

    # explicit dates: ISO first, then "5 oct", "oct 5 2026", "5/10" (day first, Singapore style)
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", phrase)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    try:
        parsed = du_parser.parse(phrase, dayfirst=True, default=datetime.combine(today, time.min))
    except (ValueError, OverflowError):
        return None
    if _first_time_token(phrase) and not re.search(r"[a-z]{3,}|\d{1,2}\s*/\s*\d{1,2}", phrase):
        return None  # a bare time such as "3pm", not a date
    return parsed.date()


# ---- times ----------------------------------------------------------------------------------
def _first_time_token(phrase: str) -> re.Match[str] | None:
    """First token that is unmistakably a time: has am/pm or minutes ('6pm', '14:30', '2.30 pm')."""
    for m in _TIME_RE.finditer(phrase):
        if m.group("ampm") or m.group("m") is not None:
            return m
    return None


def resolve_time(text: str) -> time | None:
    """Resolve a time phrase ('2pm', '14:30', '2.30 pm', 'noon'). None if no time present."""
    phrase = _clean(text)
    for word in _VAGUE_TIMES:
        if re.search(rf"\b{word}\b", phrase):
            raise DateAmbiguity(
                f"'{word}' is not a specific time. What time exactly, e.g. 10am or 15:30?"
            )
    m = _first_time_token(phrase)
    if not m:
        return None
    hour, minute = int(m.group("h")), int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


# ---- combined -------------------------------------------------------------------------------
def resolve_moment(text: str, *, now: datetime, tz: tzinfo = SGT) -> datetime:
    """Resolve 'wednesday 2pm', 'tomorrow 15:30', '5 oct 6pm', '2026-10-07T14:00' to a datetime.

    Raises DateAmbiguity when the day or the time is missing or vague.
    """
    raw = text.strip()
    try:
        iso = datetime.fromisoformat(raw)
        return iso if iso.tzinfo else iso.replace(tzinfo=tz)
    except ValueError:
        pass
    phrase = _clean(raw)
    time_match = _first_time_token(phrase)
    at = resolve_time(phrase)
    # the time token may be glued to a date like "5 oct 6pm": strip the time part for the day
    day_phrase = phrase
    if at is not None and time_match:
        day_phrase = _clean(phrase[: time_match.start()] + " " + phrase[time_match.end() :])
    day = resolve_day(day_phrase, now=now, tz=tz) if day_phrase else _today(now, tz)
    if day is None:
        raise DateAmbiguity(
            f"I could not work out the day in '{raw}'. Which date do you mean, e.g. "
            f"'tomorrow', 'next Tuesday' or '7 Oct'?"
        )
    if at is None:
        raise DateAmbiguity(f"What time on {day:%A %d %b}? For example 10am or 15:30.")
    return datetime.combine(day, at, tz)


def resolve_window(text: str, *, now: datetime, tz: tzinfo = SGT) -> Window:
    """Resolve a listing phrase to a half-open window: 'today', 'next week', '5 oct to 9 oct'."""
    phrase = _clean(text)
    today = _today(now, tz)
    if phrase in {"", "upcoming", "soon", "next few days"}:
        start = datetime.combine(today, time.min, tz)
        return Window(start, start + timedelta(days=7), "the next 7 days")
    if phrase in {"this week", "week"}:
        start = datetime.combine(today, time.min, tz)
        end = datetime.combine(_week_start(today) + timedelta(days=7), time.min, tz)
        return Window(start, end, "the rest of this week")
    if phrase in {"next week", "following week"}:
        start = datetime.combine(_week_start(today) + timedelta(days=7), time.min, tz)
        return Window(start, start + timedelta(days=7), "next week")
    if phrase in {"this weekend", "weekend", "coming weekend"}:
        delta = (5 - today.weekday()) % 7
        sat = today + timedelta(days=delta if today.weekday() < 5 or delta else 0)
        if today.weekday() >= 5:
            sat = _week_start(today) + timedelta(days=5)
        start = datetime.combine(sat, time.min, tz)
        return Window(start, start + timedelta(days=2), f"the weekend of {sat:%d %b}")
    if phrase in {"this month", "month"}:
        start = datetime.combine(today, time.min, tz)
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return Window(start, datetime.combine(first_next, time.min, tz), "the rest of this month")
    m = re.fullmatch(r"next (\d{1,2}) days?", phrase)
    if m:
        start = datetime.combine(today, time.min, tz)
        n = int(m.group(1))
        return Window(start, start + timedelta(days=n), f"the next {n} days")

    parts = [p for p in _RANGE_SPLIT.split(phrase) if p]
    if len(parts) == 2:
        first = resolve_day(parts[0], now=now, tz=tz)
        second = resolve_day(parts[1], now=now, tz=tz)
        if first and second:
            if second < first:
                raise DateAmbiguity(
                    f"The range '{text}' ends ({second:%d %b}) before it starts ({first:%d %b})."
                )
            start = datetime.combine(first, time.min, tz)
            end = datetime.combine(second + timedelta(days=1), time.min, tz)
            return Window(start, end, f"{first:%d %b} to {second:%d %b}")

    day = resolve_day(phrase, now=now, tz=tz)
    if day is None:
        raise DateAmbiguity(
            f"I could not work out the dates in '{text}'. Try 'today', 'next week', "
            f"'next Tuesday' or '5 Oct to 9 Oct'."
        )
    return _day_window(day, tz, f"{day:%A %d %b}")
