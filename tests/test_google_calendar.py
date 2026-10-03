"""Google backend: event parsing, bounded list parameters, payloads and notification control."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from outlook_calendar_agent.google_calendar import EVENT_FIELDS, GoogleCalendarService
from outlook_calendar_agent.models import (
    CalendarEvent,
    DeleteDraft,
    EventDraft,
    EventSnapshot,
    UpdateDraft,
)
from outlook_calendar_agent.timeutil import SGT, parse_google_datetime

from .conftest import GOOGLE_EVENT_ID, FakeAuth, FakeGoogleClient, google_event

START = datetime(2026, 10, 7, 14, 0, tzinfo=SGT)


def _service(client: FakeGoogleClient) -> GoogleCalendarService:
    return GoogleCalendarService(client, FakeAuth(), SGT)  # type: ignore[arg-type]


# ---- parsing -------------------------------------------------------------------------------
def test_from_google_timed_event() -> None:
    event = CalendarEvent.from_google(
        google_event(
            attendees=[
                {"email": "me@example.com", "organizer": True, "self": True},
                {"email": "alice@example.com", "displayName": "Alice", "optional": True},
            ]
        ),
        SGT,
    )
    assert event.start == START and event.end == START + timedelta(hours=1)
    assert event.location == "Room 4"
    assert event.organizer_email == "me@example.com" and event.is_organizer is True
    assert event.event_type == "singleInstance" and not event.is_cancelled
    # The organizer entry is dropped; only real attendees remain.
    assert [(a.email, a.type) for a in event.attendees] == [("alice@example.com", "optional")]
    assert event.change_key == '"etag-1"'


def test_from_google_all_day_recurring_and_cancelled() -> None:
    all_day = CalendarEvent.from_google(
        {
            "id": "a",
            "summary": "Offsite",
            "start": {"date": "2026-10-06"},
            "end": {"date": "2026-10-07"},
        },
        SGT,
    )
    assert all_day.is_all_day and all_day.start == datetime(2026, 10, 6, tzinfo=SGT)
    instance = CalendarEvent.from_google(google_event(recurringEventId="master"), SGT)
    assert instance.event_type == "occurrence"
    master = CalendarEvent.from_google(google_event(recurrence=["RRULE:FREQ=WEEKLY"]), SGT)
    assert master.event_type == "seriesMaster"
    gone = CalendarEvent.from_google(google_event(status="cancelled"), SGT)
    assert gone.is_cancelled


def test_parse_google_datetime_variants() -> None:
    assert parse_google_datetime({"dateTime": "2026-10-07T06:00:00Z"}).astimezone(SGT).hour == 14
    naive = parse_google_datetime({"dateTime": "2026-10-07T14:00:00", "timeZone": "Asia/Singapore"})
    assert naive == START
    with pytest.raises(ValueError):
        parse_google_datetime({})


# ---- reads ---------------------------------------------------------------------------------
def test_list_events_is_bounded_and_expands_instances(google: FakeGoogleClient) -> None:
    events = _service(google).list_events(
        START.replace(hour=0), START.replace(hour=0) + timedelta(days=1)
    )
    call = google.calls[-1]
    assert call.path == "/calendars/primary/events"
    assert call.params["timeMin"] == "2026-10-06T16:00:00Z"
    assert call.params["timeMax"] == "2026-10-07T16:00:00Z"
    assert call.params["singleEvents"] == "true"
    assert call.params["orderBy"] == "startTime"
    assert call.params["timeZone"] == "Asia/Singapore"
    assert "items(" in call.params["fields"]
    assert [e.id for e in events] == [GOOGLE_EVENT_ID]
    with pytest.raises(ValueError):
        _service(google).list_events(START, START)


def test_get_event_quotes_id_and_limits_fields(google: FakeGoogleClient) -> None:
    google.events_by_id["a b/c"] = google_event(event_id="a b/c")
    _service(google).get_event("a b/c")
    assert google.calls[-1].path == "/calendars/primary/events/a%20b%2Fc"
    assert google.calls[-1].params == {"fields": EVENT_FIELDS}


def test_get_me_uses_userinfo(google: FakeGoogleClient) -> None:
    assert _service(google).get_me() == {"displayName": "Chia Geng", "mail": "me@example.com"}


def test_find_conflicts_excludes_self(google: FakeGoogleClient) -> None:
    google.events = [
        google_event(),
        google_event(event_id="other", start="2026-10-07T14:30:00+08:00"),
    ]
    conflicts = _service(google).find_conflicts(
        START, START + timedelta(hours=1), exclude_id=GOOGLE_EVENT_ID
    )
    assert [c.id for c in conflicts] == ["other"]


# ---- writes --------------------------------------------------------------------------------
def test_create_event_controls_send_updates(google: FakeGoogleClient) -> None:
    service = _service(google)
    quiet = EventDraft(subject="Review", start=START, end=START + timedelta(minutes=45))
    service.create_event(quiet)
    assert google.calls[-1].method == "POST"
    assert google.calls[-1].params["sendUpdates"] == "none"
    assert google.calls[-1].json == quiet.to_google_payload(SGT)
    loud = EventDraft(
        subject="Review",
        start=START,
        end=START + timedelta(minutes=45),
        attendees=[],
        send_invitations=True,
    )
    service.create_event(loud)
    assert google.calls[-1].params["sendUpdates"] == "all"


def test_update_and_delete_use_if_match_and_send_updates(google: FakeGoogleClient) -> None:
    service = _service(google)
    snapshot = EventSnapshot.from_event(CalendarEvent.from_google(google_event(), SGT))
    update = UpdateDraft(
        original=snapshot, start=START + timedelta(hours=2), end=START + timedelta(hours=3)
    )
    service.update_event(update)
    call = google.calls[-1]
    assert call.method == "PATCH" and call.path == f"/calendars/primary/events/{GOOGLE_EVENT_ID}"
    assert call.headers["If-Match"] == '"etag-1"'
    assert call.params["sendUpdates"] == "none"
    assert call.json == {
        "start": {"dateTime": "2026-10-07T16:00:00+08:00", "timeZone": "Asia/Singapore"},
        "end": {"dateTime": "2026-10-07T17:00:00+08:00", "timeZone": "Asia/Singapore"},
    }
    service.delete_event(DeleteDraft(original=snapshot, notify_attendees=True))
    call = google.calls[-1]
    assert call.method == "DELETE" and call.headers["If-Match"] == '"etag-1"'
    assert call.params == {"sendUpdates": "all"}
