from datetime import datetime, timedelta

import pytest

from outlook_calendar_agent.models import (
    Attendee,
    CalendarEvent,
    DeleteDraft,
    Draft,
    EventDraft,
    EventSnapshot,
    UpdateDraft,
)
from outlook_calendar_agent.timeutil import SGT

from .conftest import graph_event

START = datetime(2026, 10, 7, 14, 0, tzinfo=SGT)
END = START + timedelta(minutes=45)


def test_attendee_parse_forms() -> None:
    plain = Attendee.parse("alice@example.com")
    named = Attendee.parse("Alice Tan <alice@example.com>", type="optional")
    assert plain.email == "alice@example.com" and plain.name is None
    assert named.name == "Alice Tan" and named.type == "optional"
    assert named.to_graph() == {
        "emailAddress": {"address": "alice@example.com", "name": "Alice Tan"},
        "type": "optional",
    }
    with pytest.raises(ValueError):
        Attendee.parse("alice")


def test_create_payload_is_minimal_and_in_singapore_time() -> None:
    draft = EventDraft(subject="Project review", start=START, end=END, location="Room 4")
    payload = draft.to_graph_payload(SGT)
    assert payload == {
        "subject": "Project review",
        "start": {"dateTime": "2026-10-07T14:00:00", "timeZone": "Asia/Singapore"},
        "end": {"dateTime": "2026-10-07T14:45:00", "timeZone": "Asia/Singapore"},
        "location": {"displayName": "Room 4"},
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 1440,
    }
    # Deferred features must never be set implicitly.
    for forbidden in ("isOnlineMeeting", "onlineMeetingProvider", "recurrence", "organizer"):
        assert forbidden not in payload


def test_create_requires_end_after_start_and_aware_times() -> None:
    with pytest.raises(ValueError, match="end must be after start"):
        EventDraft(subject="x", start=START, end=START)
    with pytest.raises(ValueError, match="time zone"):
        EventDraft(subject="x", start=START.replace(tzinfo=None), end=END)


def test_invitations_flag_controls_notification_status() -> None:
    attendee = Attendee(email="alice@example.com", name="Alice")
    silent = EventDraft(subject="x", start=START, end=END, attendees=[attendee])
    assert not silent.invitations_will_be_sent
    draft = EventDraft(
        subject="x", start=START, end=END, attendees=[attendee], send_invitations=True
    )
    assert draft.invitations_will_be_sent
    graph_attendee = draft.to_graph_payload()["attendees"][0]
    assert graph_attendee["emailAddress"]["address"] == "alice@example.com"
    assert draft.to_google_payload()["attendees"] == [
        {"email": "alice@example.com", "displayName": "Alice"}
    ]


def test_google_payload_is_minimal_and_in_singapore_time() -> None:
    draft = EventDraft(subject="Project review", start=START, end=END, location="Room 4", body="b")
    payload = draft.to_google_payload(SGT)
    assert payload == {
        "summary": "Project review",
        "start": {"dateTime": "2026-10-07T14:00:00+08:00", "timeZone": "Asia/Singapore"},
        "end": {"dateTime": "2026-10-07T14:45:00+08:00", "timeZone": "Asia/Singapore"},
        "location": "Room 4",
        "description": "b",
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 1440}]},
    }
    for forbidden in ("conferenceData", "recurrence", "organizer"):
        assert forbidden not in payload


def test_calendar_event_from_graph() -> None:
    event = CalendarEvent.from_graph(
        graph_event(attendees=[{"emailAddress": {"address": "a@example.com"}, "type": "required"}])
    )
    assert event.start == datetime(2026, 10, 7, 14, 0, tzinfo=SGT)
    assert event.location == "Room 4"
    assert event.organizer_email == "me@example.com"
    assert event.attendees[0].email == "a@example.com"
    assert event.overlaps(START, END)
    assert not event.overlaps(END + timedelta(hours=1), END + timedelta(hours=2))


def test_update_patch_contains_only_changed_fields() -> None:
    original = EventSnapshot.from_event(CalendarEvent.from_graph(graph_event()))
    draft = UpdateDraft(
        original=original, start=START + timedelta(hours=1), end=END + timedelta(hours=1)
    )
    patch = draft.to_graph_patch(SGT)
    assert set(patch) == {"start", "end"}
    assert patch["start"]["dateTime"] == "2026-10-07T15:00:00"
    subject_only = UpdateDraft(original=original, subject="Renamed")
    assert subject_only.to_graph_patch() == {"subject": "Renamed"}


def test_update_requires_a_change_and_reports_notification_status() -> None:
    original = EventSnapshot.from_event(CalendarEvent.from_graph(graph_event()))
    with pytest.raises(ValueError, match="no changes"):
        UpdateDraft(original=original, subject=original.subject)
    with_attendees = EventSnapshot.from_event(
        CalendarEvent.from_graph(
            graph_event(attendees=[{"emailAddress": {"address": "a@example.com"}}])
        )
    )
    assert not UpdateDraft(original=with_attendees, subject="New").notices_will_be_sent
    loud = UpdateDraft(original=with_attendees, subject="New", notify_attendees=True)
    assert loud.notices_will_be_sent
    assert loud.to_google_patch() == {"summary": "New"}


def test_snapshot_matches_uses_change_key() -> None:
    event = CalendarEvent.from_graph(graph_event())
    snapshot = EventSnapshot.from_event(event)
    assert snapshot.matches(event)
    assert not snapshot.matches(CalendarEvent.from_graph(graph_event(change_key="ck-2")))


def test_draft_roundtrip_json() -> None:
    draft = Draft(
        id="d-abc123",
        created_at=datetime(2026, 10, 3, tzinfo=SGT),
        payload=DeleteDraft(
            original=EventSnapshot.from_event(CalendarEvent.from_graph(graph_event()))
        ),
    )
    restored = Draft.model_validate_json(draft.model_dump_json())
    assert restored.kind == "delete"
    assert restored.payload.original.start == datetime(2026, 10, 7, 14, 0, tzinfo=SGT)


def test_links_and_with_link_edge_cases() -> None:
    from outlook_calendar_agent.models import extract_links, with_link

    assert extract_links("Join at https://zoom.us/j/5, dial-in below.") == ["https://zoom.us/j/5"]
    html = "<p>Agenda</p><p>Meeting link: https://a/b</p>"
    assert (
        with_link(html, "https://c/d", html=True)
        == "<p>Agenda</p>\n<p>Meeting link: https://c/d</p>"
    )
    assert with_link("notes\nMeeting link: https://a/b", None) == "notes"
    assert with_link(None, "https://x/y") == "Meeting link: https://x/y"
