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


@pytest.mark.parametrize(
    "subject",
    [
        # names and topics that merely look like days
        "Call with Wed Lee",
        "Meeting with Sun Wei",
        "Black Friday sale",
        "SAT prep class",
        "Wed. standup",
        # leading words that are part of the title
        "Book club",
        "New Year party",
        "The Weeknd Concert",
        "A&E appointment",
        "A-level results",
        "Draft review",
        # numbers that are not a time or a length
        "Midnight mass",
        "Chapter 3:16 bible class",
        "24 Hours of Le Mans watch party",
        "Call in 5 mins",
        # "at" and place words used as ordinary words
        "Look at budget",
        "Lunch at 12",
        "Chat at length with HR",
        "Meeting with Bob at his office about the lease",
        "Review bar chart",
        "Review court documents",
        "Review St John proposal",
        "Meeting room booking review",
        "Call hospital about results",
        "开会",
    ],
)
def test_titles_left_alone(subject) -> None:
    assert clean_subject_and_location(subject) == (subject, None)


@pytest.mark.parametrize(
    ("subject", "location", "expected"),
    [
        # a supplied location is removed as whole words only, at the end or after "at"
        ("Shawn birthday", "Shaw", ("Shawn birthday", "Shaw")),
        ("Homework review", "Home", ("Homework review", "Home")),
        ("Office hours", "office", ("Office hours", "Office")),
        ("lunch at shaw centre with bob", "Shaw centre", ("Lunch with bob", "Shaw Centre")),
        # times with their preposition
        ("Review Q3 at 3pm", None, ("Review Q3", None)),
        ("Lunch at Shaw at 12pm", None, ("Lunch", "Shaw")),
        ("Meeting 4-6pm", None, ("Meeting", None)),
        ("meeting 14:30", None, ("Meeting", None)),
        ("sync for 2 hours", None, ("Sync", None)),
        # days
        ("please book a meeting next tue 3pm", None, ("Meeting", None)),
        ("sat lunch", None, ("Lunch", None)),
        ("lunch tomorrow shaw centre", None, ("Lunch", "Shaw Centre")),
        (
            "dinner with Alice at Marina Bay Sands 7pm",
            None,
            ("Dinner with Alice", "Marina Bay Sands"),
        ),
        ("Weeknd Concert at Stadium", None, ("Weeknd Concert", "Stadium")),
        ("", None, ("", None)),
    ],
)
def test_clean_edge_cases(subject, location, expected) -> None:
    assert clean_subject_and_location(subject, location) == expected


def test_long_or_repetitive_input_is_fast_and_untouched() -> None:
    import time

    started = time.monotonic()
    long_title = "a " * 50_000
    assert clean_subject_and_location(long_title)[0] == long_title.strip()
    assert time.monotonic() - started < 1


def test_format_duration() -> None:
    assert format_duration(60) == "1 hour"
    assert format_duration(120) == "2 hours"
    assert format_duration(45) == "45 minutes"
    assert format_duration(90) == "1 hour 30 minutes"
