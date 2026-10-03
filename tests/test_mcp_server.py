"""MCP server: tool listing, reads, drafts and the confirm gate, via an in-process client."""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp import Client

from outlook_calendar_agent.mcp_server import build_server

from .conftest import GOOGLE_EVENT_ID, FakeGoogleClient, google_event


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def _call(server, name: str, **args):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return asyncio.run(go())


@pytest.fixture
def server(grt):
    return build_server(rt_factory=lambda: grt)


def test_tool_catalogue(server) -> None:
    async def go():
        async with Client(server) as client:
            return await client.list_tools()

    tools = asyncio.run(go())
    names = {t.name for t in tools.tools}
    assert names == {
        "now",
        "list_calendars",
        "list_events",
        "find_free_slots",
        "list_tasks",
        "draft_create_event",
        "draft_update_event",
        "draft_delete_event",
        "list_drafts",
        "confirm_draft",
        "discard_draft",
    }
    schema = next(t for t in tools.tools if t.name == "draft_create_event").input_schema
    assert schema["required"] == ["subject", "when"]


def test_reads(server, google: FakeGoogleClient) -> None:
    assert "Asia/Singapore" in _text(_call(server, "now"))
    assert "me@example.com  [primary, writable]" in _text(_call(server, "list_calendars"))
    out = _text(_call(server, "list_events", when="2026-10-07"))
    assert "Project review" in out and "14:00–15:00" in out
    out = _text(_call(server, "find_free_slots", day="2026-10-07", duration_minutes=60))
    assert "09:00–14:00" in out and "15:00–18:00" in out
    assert google.write_calls == []


def test_ambiguity_and_errors_are_returned_not_raised(server) -> None:
    out = _text(_call(server, "draft_create_event", subject="X", when="tomorrow morning"))
    assert out.startswith("QUESTION:") and "specific time" in out
    out = _text(_call(server, "draft_create_event", subject="Dinner", when="2026-10-07T19:00"))
    # no length given: a labelled 1 hour default, never a silent guess
    assert "End              : 2026-10-07 20:00" in out
    assert 'Duration         : 1 hour (default; reply e.g. "make it 2 hours" to change)' in out
    out = _text(
        _call(server, "draft_create_event", subject="Dinner", when="2026-10-07T19:00", end="9pm")
    )
    assert "End              : 2026-10-07 21:00" in out  # time-only end lands on the start's day
    out = _text(
        _call(
            server,
            "draft_create_event",
            subject="X",
            when="2026-10-07T14:00",
            duration_minutes=30,
            calendar="Family",
        )
    )
    assert out.startswith("ERROR:") and "read-only" in out
    out = _text(_call(server, "confirm_draft", draft_id="d-000000", user_reply="yes"))
    assert out.startswith("ERROR:") and "not found" in out


def test_draft_then_confirm_requires_literal_yes(
    server, grt, google: FakeGoogleClient, google_audit_entries
) -> None:
    out = _text(
        _call(
            server,
            "draft_create_event",
            subject="Standup",
            when="2026-10-07T09:00",
            duration_minutes=15,
            location="Zoom",
        )
    )
    assert "PROPOSED CHANGE" in out and "Draft saved as d-" in out
    draft_id = out.split("Draft saved as ")[1].split(".")[0]
    assert google.write_calls == []

    # A clarifying reply keeps the draft; a cancel word discards it. Neither writes anything.
    out = _text(_call(server, "confirm_draft", draft_id=draft_id, user_reply="yes please"))
    assert "Not confirmed" in out and "kept" in out and google.write_calls == []
    assert draft_id in _text(_call(server, "list_drafts"))
    out = _text(_call(server, "confirm_draft", draft_id=draft_id, user_reply="no"))
    assert "discarded" in out and google.write_calls == []
    assert [e["stage"] for e in google_audit_entries()] == ["proposed", "rejected"]

    out = _text(
        _call(
            server,
            "draft_create_event",
            subject="Standup",
            when="2026-10-07T09:00",
            duration_minutes=15,
        )
    )
    draft_id = out.split("Draft saved as ")[1].split(".")[0]
    out = _text(_call(server, "confirm_draft", draft_id=draft_id, user_reply="yes"))
    assert out.startswith("Created: Standup")
    assert len(google.write_calls) == 1 and google.write_calls[0].method == "POST"
    assert google_audit_entries()[-1]["stage"] == "succeeded"
    assert google_audit_entries()[-3]["via"] == "mcp"  # the 'proposed' entry is tagged
    assert "No pending drafts" in _text(_call(server, "list_drafts"))


def test_update_and_delete_drafts(server, google: FakeGoogleClient) -> None:
    out = _text(
        _call(
            server,
            "draft_update_event",
            find="project review",
            on="2026-10-07",
            new_when="2026-10-07T16:00",
        )
    )
    assert "16:00 (Asia/Singapore)   [CHANGED]" in out
    draft_id = out.split("Draft saved as ")[1].split(".")[0]
    assert _text(_call(server, "confirm_draft", draft_id=draft_id, user_reply="yes")).startswith(
        "Updated:"
    )
    assert google.write_calls[-1].method == "PATCH"

    out = _text(_call(server, "draft_delete_event", event_id=GOOGLE_EVENT_ID))
    assert "PROPOSED DELETION" in out
    draft_id = out.split("Draft saved as ")[1].split(".")[0]
    assert "discarded" in _text(_call(server, "discard_draft", draft_id=draft_id))
    assert [c.method for c in google.write_calls] == ["PATCH"]


def test_text_result_has_no_json_wrapping(server) -> None:
    result = _call(server, "now")
    assert result.content[0].type == "text"
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.content[0].text)


def test_links_and_reminders_in_drafts_and_listings(server, google: FakeGoogleClient) -> None:
    out = _text(
        _call(
            server,
            "draft_create_event",
            subject="Sync",
            when="2026-10-07T16:00",
            duration_minutes=30,
            link="https://zoom.us/j/555",
            reminder_minutes_before=120,
        )
    )
    assert "Meeting link     : https://zoom.us/j/555" in out
    assert "Reminder         : 2 hours before" in out
    draft_id = out.split("Draft saved as ")[1].split(".")[0]
    _call(server, "confirm_draft", draft_id=draft_id, user_reply="yes")
    payload = google.write_calls[-1].json
    assert payload["description"] == "Meeting link: https://zoom.us/j/555"
    assert payload["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 120}],
    }

    bad = _text(
        _call(
            server,
            "draft_create_event",
            subject="S",
            when="2026-10-07T16:00",
            duration_minutes=30,
            link="zoom meeting",
        )
    )
    assert bad.startswith("ERROR:") and "not a valid http" in bad

    # listings show the first link found on the event
    google.events = [google_event(description="Agenda\nMeeting link: https://meet.example/q")]
    listing = _text(_call(server, "list_events", when="2026-10-07"))
    assert "🔗 https://meet.example/q" in listing

    # updates can attach a link and change the reminder on an existing event
    out = _text(
        _call(
            server,
            "draft_update_event",
            event_id=GOOGLE_EVENT_ID,
            new_link="https://teams.example/abc",
            new_reminder_minutes_before=60,
        )
    )
    assert "https://teams.example/abc" in out and "1 hour before" in out


def test_update_search_uses_the_whole_window(server, google: FakeGoogleClient, monkeypatch) -> None:
    """'move my dentist appointment next week' must search all of next week, not just Monday."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from outlook_calendar_agent import agent_api

    monkeypatch.setattr(
        agent_api, "_now", lambda rt: datetime(2026, 10, 3, 9, 0, tzinfo=ZoneInfo("Asia/Singapore"))
    )
    out = _text(
        _call(server, "draft_update_event", find="project review", on="next week", new_when="4pm")
    )
    assert "PROPOSED CHANGE" in out, out
    search = [c for c in google.calls if c.path.endswith("/events") and c.method == "GET"][0]
    assert search.params["timeMin"] == "2026-10-04T16:00:00Z"  # Mon 5 Oct 00:00 SGT
    assert search.params["timeMax"] == "2026-10-11T16:00:00Z"  # Mon 12 Oct 00:00 SGT
    # the time-only new_when stays on the event's own day (7 Oct), not on 'today'
    assert "2026-10-07 16:00 (Asia/Singapore)   [CHANGED]" in out


def test_reminder_tri_state(server, google: FakeGoogleClient) -> None:
    none = _text(
        _call(
            server,
            "draft_create_event",
            subject="A",
            when="2026-10-07T10:00",
            duration_minutes=30,
            reminder_minutes_before=0,
        )
    )
    assert "Reminder         : none" in none
    _call(
        server,
        "confirm_draft",
        draft_id=none.split("Draft saved as ")[1].split(".")[0],
        user_reply="yes",
    )
    assert google.write_calls[-1].json["reminders"] == {"useDefault": False, "overrides": []}
    default = _text(
        _call(
            server,
            "draft_create_event",
            subject="B",
            when="2026-10-07T11:00",
            duration_minutes=30,
            reminder_minutes_before=-1,
        )
    )
    assert "Reminder         : calendar default" in default
    _call(
        server,
        "confirm_draft",
        draft_id=default.split("Draft saved as ")[1].split(".")[0],
        user_reply="yes",
    )
    assert "reminders" not in google.write_calls[-1].json
    upd = _text(
        _call(
            server, "draft_update_event", event_id=GOOGLE_EVENT_ID, new_reminder_minutes_before=-1
        )
    )
    assert "Reminder" in upd and "calendar default" in upd or "ERROR" in upd


def test_subject_and_location_are_cleaned(server, google: FakeGoogleClient) -> None:
    out = _text(
        _call(
            server,
            "draft_create_event",
            subject="Another meeting Shaw centre",
            when="2026-10-07T16:00",
            end="6pm",
        )
    )
    assert "Subject          : Meeting" in out
    assert "Location         : Shaw Centre" in out
    assert (
        "Duration         : 2 hours" in out
        and "default" not in out.split("Duration")[1].splitlines()[0]
    )
    out = _text(
        _call(
            server,
            "draft_create_event",
            subject="Monday lunch 12pm shaw centre",
            when="2026-10-07T12:00",
        )
    )
    assert "Subject          : Lunch" in out and "Location         : Shaw Centre" in out


def test_past_start_asks_which_day(server, monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from outlook_calendar_agent import agent_api

    monkeypatch.setattr(
        agent_api,
        "_now",
        lambda rt: datetime(2026, 10, 3, 18, 0, tzinfo=ZoneInfo("Asia/Singapore")),
    )
    out = _text(_call(server, "draft_create_event", subject="Meeting", when="4pm", end="6pm"))
    assert out.startswith("QUESTION:") and "already passed" in out
