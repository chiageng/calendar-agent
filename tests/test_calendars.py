"""Multiple calendars: listing, --calendar resolution and per-calendar paths."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from outlook_calendar_agent.cli import app
from outlook_calendar_agent.errors import AgentError, AmbiguousEventError
from outlook_calendar_agent.write_flow import resolve_calendar

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
    assert resolve_calendar(grt, None) == "primary"
    assert resolve_calendar(grt, "Primary") == "primary"
    assert resolve_calendar(grt, "work") == "work123@group.calendar.google.com"
    assert (
        resolve_calendar(grt, "fam456@group.calendar.google.com")
        == "fam456@group.calendar.google.com"
    )
    with pytest.raises(AgentError, match="No calendar named"):
        resolve_calendar(grt, "dentist")
    grt.calendar._client.calendars.append(  # type: ignore[attr-defined]
        {"id": "w2@group.calendar.google.com", "summary": "Work 2", "accessRole": "writer"}
    )
    with pytest.raises(AmbiguousEventError, match="Several calendars"):
        resolve_calendar(grt, "wor")


def test_events_with_calendar_option_targets_that_calendar(grt, google: FakeGoogleClient) -> None:
    result = runner.invoke(
        app, ["events", "--from", "2026-10-07", "--to", "2026-10-08", "--calendar", "Work"]
    )
    assert result.exit_code == 0, result.output
    assert "Calendar: work123@group.calendar.google.com" in result.output
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
    assert "Calendar         : work123@group.calendar.google.com" in result.output
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
