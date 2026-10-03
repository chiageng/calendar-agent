from datetime import datetime, timedelta

from outlook_calendar_agent.formatting import (
    format_conflicts,
    format_create_preview,
    format_delete_preview,
    format_event_list,
    format_signed_in,
    format_update_preview,
)
from outlook_calendar_agent.models import (
    Attendee,
    CalendarEvent,
    DeleteDraft,
    EventDraft,
    EventSnapshot,
    UpdateDraft,
)
from outlook_calendar_agent.timeutil import SGT

from .conftest import graph_event

START = datetime(2026, 10, 5, 10, 0, tzinfo=SGT)


def test_signed_in_line() -> None:
    assert format_signed_in({"displayName": "Chia Geng", "mail": "me@example.com"}) == (
        "Signed in as: Chia Geng <me@example.com>"
    )


def test_event_list_groups_by_day_in_singapore_time() -> None:
    events = [
        CalendarEvent.from_graph(
            graph_event(
                "1",
                "Team stand-up",
                "2026-10-05T02:00:00",
                "2026-10-05T02:30:00",
                tz="UTC",
                location=None,
            )
        ),
        CalendarEvent.from_graph(
            graph_event(
                "2",
                "Project review",
                "2026-10-05T14:00:00",
                "2026-10-05T15:00:00",
                location={"displayName": "Teams"},
            )
        ),
        CalendarEvent.from_graph(
            graph_event("3", "Offsite", "2026-10-06T00:00:00", "2026-10-07T00:00:00", isAllDay=True)
        ),
    ]
    text = format_event_list(events, START.replace(hour=0), START + timedelta(days=7))
    assert "Asia/Singapore" in text
    assert "2026-10-05\n  10:00–10:30  Team stand-up | me@example.com" in text
    assert "  14:00–15:00  Project review | Teams | me@example.com" in text
    assert "2026-10-06\n  all day" in text


def test_empty_event_list() -> None:
    assert format_event_list([], START, START + timedelta(days=1)).endswith("(no events)")


def test_create_preview_has_all_required_fields() -> None:
    draft = EventDraft(
        subject="Project review",
        start=START,
        end=START + timedelta(minutes=45),
        location="Room 4",
        attendees=[Attendee(email="alice@example.com", name="Alice")],
        send_invitations=True,
    )
    text = format_create_preview(draft)
    for needle in (
        "Action",
        "CREATE",
        "Event ID",
        "Subject",
        "Project review",
        "Start",
        "2026-10-05 10:00 (Asia/Singapore)",
        "End",
        "2026-10-05 10:45 (Asia/Singapore)",
        "Calendar",
        "Location",
        "Room 4",
        "Meeting link",
        "Attendees",
        "Alice <alice@example.com>",
        "Invitations sent",
        "YES",
    ):
        assert needle in text, needle


def test_update_preview_marks_changes() -> None:
    original = EventSnapshot.from_event(CalendarEvent.from_graph(graph_event()))
    draft = UpdateDraft(
        original=original,
        start=original.start + timedelta(hours=1),
        end=original.end + timedelta(hours=1),
    )
    text = format_update_preview(draft)
    assert "UPDATE" in text and original.id in text
    assert (
        "2026-10-07 14:00 (Asia/Singapore)  ->  2026-10-07 15:00 (Asia/Singapore)   [CHANGED]"
        in text
    )
    assert "Update notices sent : no" in text


def test_delete_preview_and_conflicts() -> None:
    draft = DeleteDraft(original=EventSnapshot.from_event(CalendarEvent.from_graph(graph_event())))
    text = format_delete_preview(draft)
    assert "DELETE" in text and "PERMANENT" in text and draft.original.id in text
    assert "2026-10-07 14:00 (Asia/Singapore)" in text
    assert format_conflicts([]) == "Conflicts: none in the proposed window."
    assert "1 existing event(s)" in format_conflicts([CalendarEvent.from_graph(graph_event())])
