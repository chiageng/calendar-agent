"""Multiple calendars: listing, --calendar resolution and per-calendar paths."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from outlook_calendar_agent.calendars import find_conflicts_everywhere, resolve_calendar
from outlook_calendar_agent.cli import app
from outlook_calendar_agent.errors import AgentError

from .conftest import GOOGLE_EVENT_ID, FakeGoogleClient, FakeGraphClient

runner = CliRunner()


def test_calendars_command_google(grt, google: FakeGoogleClient) -> None:
    result = runner.invoke(app, ["calendars"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "me@example.com  [primary, writable]"
    assert "Work  [writable]" in result.output
    assert "    id: work123@group.calendar.google.com" in result.output
    assert "Family" in result.output and "Family  [" not in result.output  # read-only
    assert google.calls[-1].path == "/users/me/calendarList"


def test_calendars_command_graph(rt, graph: FakeGraphClient) -> None:
    result = runner.invoke(app, ["calendars"])
    assert result.exit_code == 0, result.output
    assert "Calendar  [primary, writable]" in result.output


def test_resolve_calendar_by_name_id_and_errors(grt) -> None:
    backend = grt.calendar
    assert resolve_calendar(backend, None).id == "primary"
    assert resolve_calendar(backend, "  ").id == "primary"  # blank means primary
    assert resolve_calendar(backend, "Primary").id == "primary"
    work = resolve_calendar(backend, "work")
    assert work.id == "work123@group.calendar.google.com" and work.name == "Work"
    # IDs are compared case-insensitively
    assert resolve_calendar(backend, "FAM456@group.calendar.google.com").name == "Family"
    with pytest.raises(AgentError, match="No calendar named"):
        resolve_calendar(backend, "dentist")
    with pytest.raises(AgentError, match="read-only"):
        resolve_calendar(backend, "Family", for_write=True)
    backend._client.calendars.append(  # type: ignore[attr-defined]
        {"id": "w2@group.calendar.google.com", "summary": "Work 2", "accessRole": "writer"}
    )
    backend._calendars_cache = None  # the list is memoised per process; reset for the test
    with pytest.raises(AgentError, match="Several calendars"):
        resolve_calendar(backend, "wor")


def test_conflicts_are_checked_across_writable_calendars(grt, google: FakeGoogleClient) -> None:
    from datetime import datetime, timedelta

    from outlook_calendar_agent.timeutil import SGT

    start = datetime(2026, 10, 7, 14, 0, tzinfo=SGT)
    target = resolve_calendar(grt.calendar, "Work")
    google.calls.clear()
    conflicts = find_conflicts_everywhere(
        grt.calendar, start, start + timedelta(hours=1), target=target
    )
    scanned = [c.path for c in google.calls if c.path.endswith("/events")]
    # primary (by its real id) and Work are scanned once each; read-only Family is skipped
    assert scanned == [
        "/calendars/me%40example.com/events",
        "/calendars/work123%40group.calendar.google.com/events",
    ]
    assert len(conflicts) == 1  # the fake returns the same event id for every calendar: deduped


def test_events_with_calendar_option_targets_that_calendar(grt, google: FakeGoogleClient) -> None:
    result = runner.invoke(
        app, ["events", "--from", "2026-10-07", "--to", "2026-10-08", "--calendar", "Work"]
    )
    assert result.exit_code == 0, result.output
    assert "Calendar: Work (work123@group.calendar.google.com)" in result.output
    assert google.calls[-1].path == "/calendars/work123%40group.calendar.google.com/events"


def test_draft_on_secondary_calendar_round_trips(grt, google: FakeGoogleClient) -> None:
    result = runner.invoke(
        app,
        [
            "draft-create",
            "--subject",
            "Standup",
            "--start",
            "2026-10-07T09:00",
            "--duration",
            "15",
            "--calendar",
            "Work",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Calendar         : Work (work123@group.calendar.google.com)" in result.output
    conflict_call = [c for c in google.calls if c.path.endswith("/events") and c.method == "GET"][
        -1
    ]
    assert conflict_call.path == "/calendars/work123%40group.calendar.google.com/events"
    draft_id = result.output.split("Draft saved as ")[1].split(".")[0]
    done = runner.invoke(app, ["create", "--draft", draft_id], input="yes\n")
    assert done.exit_code == 0, done.output
    assert google.write_calls[-1].path == "/calendars/work123%40group.calendar.google.com/events"


def test_update_keeps_calendar_of_resolved_event(grt, google: FakeGoogleClient) -> None:
    result = runner.invoke(
        app,
        [
            "draft-update",
            "--event-id",
            GOOGLE_EVENT_ID,
            "--subject",
            "Renamed",
            "--calendar",
            "Work",
        ],
    )
    assert result.exit_code == 0, result.output
    draft_id = result.output.split("Draft saved as ")[1].split(".")[0]
    done = runner.invoke(app, ["update", "--draft", draft_id], input="yes\n")
    assert done.exit_code == 0, done.output
    assert google.write_calls[-1].path.startswith(
        "/calendars/work123%40group.calendar.google.com/events/"
    )


def test_write_to_read_only_calendar_is_refused_before_drafting(
    grt, google: FakeGoogleClient
) -> None:
    result = runner.invoke(
        app,
        ["draft-create", "--subject", "X", "--start", "2026-10-07T09:00", "--calendar", "Family"],
    )
    assert result.exit_code == 1 and "read-only" in result.output
    assert (
        not list(grt.settings.drafts_dir.glob("*.json"))
        if grt.settings.drafts_dir.exists()
        else True
    )


def test_audit_records_calendar_id(grt, google: FakeGoogleClient, google_audit_entries) -> None:
    result = runner.invoke(
        app,
        [
            "draft-create",
            "--subject",
            "Standup",
            "--start",
            "2026-10-07T09:00",
            "--calendar",
            "Work",
        ],
    )
    draft_id = result.output.split("Draft saved as ")[1].split(".")[0]
    assert runner.invoke(app, ["create", "--draft", draft_id], input="yes\n").exit_code == 0
    assert google_audit_entries()[-1]["calendar_id"] == "work123@group.calendar.google.com"
