from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from outlook_calendar_agent.timeutil import (
    SGT,
    format_range,
    parse_graph_datetime,
    parse_user_datetime,
    resolve_zone,
    start_of_day,
    to_graph_datetime,
    to_query_utc,
)


def test_naive_input_is_singapore_time() -> None:
    dt = parse_user_datetime("2026-10-05T14:00")
    assert dt.tzinfo == SGT
    assert dt.astimezone(UTC) == datetime(2026, 10, 5, 6, 0, tzinfo=UTC)


def test_date_only_is_midnight_singapore() -> None:
    assert parse_user_datetime("2026-10-05") == datetime(2026, 10, 5, 0, 0, tzinfo=SGT)


def test_explicit_offset_is_respected() -> None:
    dt = parse_user_datetime("2026-10-05T06:00:00Z")
    assert dt.astimezone(SGT).hour == 14


def test_today_and_tomorrow() -> None:
    now = datetime(2026, 10, 3, 23, 30, tzinfo=SGT)
    assert parse_user_datetime("today", now=now) == datetime(2026, 10, 3, tzinfo=SGT)
    assert parse_user_datetime("tomorrow", now=now) == datetime(2026, 10, 4, tzinfo=SGT)


def test_invalid_input_has_helpful_message() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_user_datetime("next tuesday")


def test_to_graph_datetime_converts_to_singapore() -> None:
    utc_dt = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
    assert to_graph_datetime(utc_dt) == {
        "dateTime": "2026-10-05T14:00:00",
        "timeZone": "Asia/Singapore",
    }


def test_parse_graph_datetime_handles_seven_fraction_digits_and_windows_zone() -> None:
    dt = parse_graph_datetime(
        {"dateTime": "2026-10-05T14:00:00.0000000", "timeZone": "Singapore Standard Time"}
    )
    assert dt == datetime(2026, 10, 5, 14, 0, tzinfo=ZoneInfo("Asia/Singapore"))
    utc = parse_graph_datetime({"dateTime": "2026-10-05T06:00:00.0000000", "timeZone": "UTC"})
    assert utc.astimezone(SGT).hour == 14


def test_unknown_zone_raises() -> None:
    with pytest.raises(ValueError, match="Unknown time zone"):
        resolve_zone("Mars/Olympus")


def test_to_query_utc() -> None:
    assert to_query_utc(datetime(2026, 10, 5, 14, 0, tzinfo=SGT)) == "2026-10-05T06:00:00Z"


def test_start_of_day_and_format_range() -> None:
    moment = datetime(2026, 10, 5, 23, 59, tzinfo=SGT)
    assert start_of_day(moment) == datetime(2026, 10, 5, tzinfo=SGT)
    start = datetime(2026, 10, 5, 14, 0, tzinfo=SGT)
    assert format_range(start, start + timedelta(hours=1)) == (
        "2026-10-05 14:00–15:00 (Asia/Singapore)"
    )
