"""Deterministic date resolution: days, times, moments, windows and the ambiguity questions."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from outlook_calendar_agent.dates import (
    DateAmbiguity,
    resolve_day,
    resolve_moment,
    resolve_time,
    resolve_window,
)
from outlook_calendar_agent.timeutil import SGT

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=SGT)  # a Saturday


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("today", date(2026, 10, 3)),
        ("tomorrow", date(2026, 10, 4)),
        ("tmr", date(2026, 10, 4)),
        ("day after tomorrow", date(2026, 10, 5)),
        ("wednesday", date(2026, 10, 7)),  # upcoming occurrence
        ("next tuesday", date(2026, 10, 6)),  # Tuesday of next Mon-Sun week
        ("next saturday", date(2026, 10, 10)),
        ("5 oct", date(2026, 10, 5)),
        ("oct 9 2026", date(2026, 10, 9)),
        ("5/10", date(2026, 10, 5)),  # day first
        ("2026-10-07", date(2026, 10, 7)),
        ("on the 7th of october", date(2026, 10, 7)),
    ],
)
def test_resolve_day(phrase: str, expected: date) -> None:
    assert resolve_day(phrase, now=NOW) == expected


def test_resolve_day_asks_when_ambiguous() -> None:
    with pytest.raises(DateAmbiguity, match="today .* or next Saturday"):
        resolve_day("saturday", now=NOW)
    with pytest.raises(DateAmbiguity, match="has passed"):
        resolve_day("this friday", now=NOW)
    assert resolve_day("3pm", now=NOW) is None  # a bare time is not a day
    assert resolve_day("lunch", now=NOW) is None


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("2pm", time(14, 0)),
        ("2.30pm", time(14, 30)),
        ("14:30", time(14, 30)),
        ("12am", time(0, 0)),
        ("12pm", time(12, 0)),
        ("noon", time(12, 0)),
        ("midnight", time(0, 0)),
        ("5 oct", None),  # no time present
    ],
)
def test_resolve_time(phrase: str, expected: time | None) -> None:
    assert resolve_time(phrase) == expected


def test_resolve_time_rejects_vague_words() -> None:
    with pytest.raises(DateAmbiguity, match="not a specific time"):
        resolve_time("morning")


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("wednesday 2pm", datetime(2026, 10, 7, 14, 0, tzinfo=SGT)),
        ("tomorrow 15:30", datetime(2026, 10, 4, 15, 30, tzinfo=SGT)),
        ("5 oct 6pm", datetime(2026, 10, 5, 18, 0, tzinfo=SGT)),
        ("next tuesday at 2.30pm", datetime(2026, 10, 6, 14, 30, tzinfo=SGT)),
        ("noon tomorrow", datetime(2026, 10, 4, 12, 0, tzinfo=SGT)),
        ("7 oct 2026 09:15", datetime(2026, 10, 7, 9, 15, tzinfo=SGT)),
        ("2026-10-07T14:00", datetime(2026, 10, 7, 14, 0, tzinfo=SGT)),
        ("3pm", datetime(2026, 10, 3, 15, 0, tzinfo=SGT)),  # today
    ],
)
def test_resolve_moment(phrase: str, expected: datetime) -> None:
    assert resolve_moment(phrase, now=NOW) == expected


def test_resolve_moment_questions() -> None:
    with pytest.raises(DateAmbiguity, match="What time on Friday 09 Oct"):
        resolve_moment("friday", now=NOW)
    with pytest.raises(DateAmbiguity, match="not a specific time"):
        resolve_moment("tomorrow morning", now=NOW)
    with pytest.raises(DateAmbiguity, match="could not work out the day"):
        resolve_moment("sometime 3pm", now=NOW)


@pytest.mark.parametrize(
    ("phrase", "start", "end"),
    [
        ("", date(2026, 10, 3), date(2026, 10, 10)),
        ("today", date(2026, 10, 3), date(2026, 10, 4)),
        ("this week", date(2026, 10, 3), date(2026, 10, 5)),
        ("next week", date(2026, 10, 5), date(2026, 10, 12)),
        ("weekend", date(2026, 10, 3), date(2026, 10, 5)),
        ("next 3 days", date(2026, 10, 3), date(2026, 10, 6)),
        ("5 oct to 9 oct", date(2026, 10, 5), date(2026, 10, 10)),
        ("oct 5 - oct 9", date(2026, 10, 5), date(2026, 10, 10)),
        ("next tuesday", date(2026, 10, 6), date(2026, 10, 7)),
    ],
)
def test_resolve_window(phrase: str, start: date, end: date) -> None:
    window = resolve_window(phrase, now=NOW)
    assert (window.start.date(), window.end.date()) == (start, end)
    assert window.start.tzinfo == SGT


def test_resolve_window_errors() -> None:
    with pytest.raises(DateAmbiguity, match="ends .* before it starts"):
        resolve_window("9 oct to 5 oct", now=NOW)
    with pytest.raises(DateAmbiguity, match="could not work out the dates"):
        resolve_window("sometime", now=NOW)


def test_review_edge_cases() -> None:
    # zero-padded month is still a month, not a year: the past date rolls forward
    assert resolve_day("15/09", now=NOW) == date(2027, 9, 15)
    assert resolve_day("15/09/2026", now=NOW) == date(2026, 9, 15)
    # an explicit time wins over a vague word; an invalid time asks about the time, not the day
    assert resolve_moment("tomorrow evening 7pm", now=NOW) == datetime(
        2026, 10, 4, 19, 0, tzinfo=SGT
    )
    with pytest.raises(DateAmbiguity, match="not a valid time"):
        resolve_moment("tomorrow 25:00", now=NOW)
