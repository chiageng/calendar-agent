"""Deterministic title/location clean-up."""

from __future__ import annotations

import pytest

from outlook_calendar_agent.titles import clean_subject_and_location, format_duration


@pytest.mark.parametrize(
    ("subject", "location", "expected"),
    [
        ("Another meeting Shaw centre", None, ("Meeting", "Shaw Centre")),
        ("Monday lunch 12pm shaw centre", None, ("Lunch", "Shaw Centre")),
        ("Another meeting Shaw centre 4pm-6pm", None, ("Meeting", "Shaw Centre")),
        ("lunch", "shaw centre", ("Lunch", "Shaw Centre")),
        ("Meeting Shaw centre", "Shaw Centre", ("Meeting", "Shaw Centre")),
        ("Dinner at Shaw House", None, ("Dinner", "Shaw House")),
        ("Lunch @ MBS", None, ("Lunch", "MBS")),
        ("coffee with Bob at Starbucks Level 2", None, ("Coffee with Bob", "Starbucks Level 2")),
        ("a 45 minute project review", None, ("Project review", None)),
        # things that must be left alone
        ("Project review with Alice", None, ("Project review with Alice", None)),
        ("dentist appointment", None, ("Dentist appointment", None)),
        ("gym session", None, ("Gym session", None)),
        ("call about pricing", None, ("Call about pricing", None)),
        ("Weeknd Concert", "Stadium", ("Weeknd Concert", "Stadium")),
        ("the", None, ("The", None)),  # never stripped down to nothing
    ],
)
def test_clean_subject_and_location(subject, location, expected) -> None:
    assert clean_subject_and_location(subject, location) == expected


def test_format_duration() -> None:
    assert format_duration(60) == "1 hour"
    assert format_duration(120) == "2 hours"
    assert format_duration(45) == "45 minutes"
    assert format_duration(90) == "1 hour 30 minutes"
