"""Google backend through the CLI: no POST/PATCH/DELETE unless the answer is exactly ``yes``."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from outlook_calendar_agent.cli import app
from outlook_calendar_agent.errors import UserCancelledError

from .conftest import GOOGLE_EVENT_ID, FakeGoogleClient, google_event

runner = CliRunner()
CREATE_ARGS = ["--subject", "Project review", "--start", "2026-10-07T14:00", "--duration", "45"]


def _draft_id(output: str) -> str:
    match = re.search(r"Draft saved as (d-[0-9a-f]{6})", output)
    assert match, output
    return match.group(1)


def test_whoami_and_events_on_google(grt, google: FakeGoogleClient) -> None:
    assert "Signed in as: Chia Geng <me@example.com>" in runner.invoke(app, ["whoami"]).output
    result = runner.invoke(app, ["events", "--from", "2026-10-07", "--to", "2026-10-08"])
    assert result.exit_code == 0, result.output
    assert "14:00–15:00  Project review | Room 4 | me@example.com" in result.output
    assert google.write_calls == []


def test_attendees_without_flag_are_allowed_but_labelled_silent(grt, google: FakeGoogleClient):
    result = runner.invoke(
        app,
        ["draft-create", *CREATE_ARGS, "--attendee", "alice@example.com", "--no-conflict-check"],
    )
    assert result.exit_code == 0, result.output
    assert (
        "Invitations sent : no (attendees are changed on the event without an e-mail)"
        in result.output
    )
    loud = runner.invoke(
        app,
        [
            "draft-create",
            *CREATE_ARGS,
            "--attendee",
            "alice@example.com",
            "--send-invitations",
            "--no-conflict-check",
        ],
    )
    assert "Invitations sent : YES (all attendees will be e-mailed)" in loud.output
    assert google.write_calls == []


@pytest.mark.parametrize("answer", ["no\n", "Yes\n", "YES\n", "y\n", "", "\n"])
def test_google_create_never_posts_without_exact_yes(grt, google, google_audit_entries, answer):
    draft_id = _draft_id(runner.invoke(app, ["draft-create", *CREATE_ARGS]).output)
    result = runner.invoke(app, ["create", "--draft", draft_id], input=answer)
    assert result.exit_code == UserCancelledError.exit_code, result.output
    assert google.write_calls == []
    assert [e["stage"] for e in google_audit_entries()] == ["proposed", "rejected"]


def test_google_create_posts_once_after_yes(grt, google: FakeGoogleClient, google_audit_entries):
    draft_id = _draft_id(runner.invoke(app, ["draft-create", *CREATE_ARGS]).output)
    result = runner.invoke(app, ["create", "--draft", draft_id], input="yes\n")
    assert result.exit_code == 0, result.output
    posts = google.write_calls
    assert len(posts) == 1 and posts[0].method == "POST"
    assert posts[0].path == "/calendars/primary/events"
    assert posts[0].params["sendUpdates"] == "none"
    assert posts[0].json["summary"] == "Project review"
    assert posts[0].json["start"] == {
        "dateTime": "2026-10-07T14:00:00+08:00",
        "timeZone": "Asia/Singapore",
    }
    assert [e["stage"] for e in google_audit_entries()] == ["proposed", "confirmed", "succeeded"]


def test_google_update_and_delete_gates(grt, google: FakeGoogleClient):
    upd = runner.invoke(
        app, ["draft-update", "--event-id", GOOGLE_EVENT_ID, "--start", "2026-10-07T16:00"]
    )
    assert upd.exit_code == 0, upd.output
    upd_id = _draft_id(upd.output)
    assert runner.invoke(app, ["update", "--draft", upd_id], input="nope\n").exit_code == 10
    assert google.write_calls == []
    ok = runner.invoke(app, ["update", "--draft", upd_id], input="yes\n")
    assert ok.exit_code == 0, ok.output
    assert google.write_calls[-1].method == "PATCH"
    assert google.write_calls[-1].headers["If-Match"] == '"etag-1"'

    google.events_by_id[GOOGLE_EVENT_ID] = google_event(attendees=[{"email": "alice@example.com"}])
    dele = runner.invoke(app, ["draft-delete", "--event-id", GOOGLE_EVENT_ID, "--notify-attendees"])
    assert dele.exit_code == 0, dele.output
    assert "Cancellations sent : YES" in dele.output
    del_id = _draft_id(dele.output)
    assert runner.invoke(app, ["delete", "--draft", del_id], input="YES\n").exit_code == 10
    assert len(google.write_calls) == 1
    done = runner.invoke(app, ["delete", "--draft", del_id], input="yes\n")
    assert done.exit_code == 0, done.output
    assert google.write_calls[-1].method == "DELETE"
    assert google.write_calls[-1].params == {"sendUpdates": "all"}


def test_google_recurring_instances_are_refused(grt, google: FakeGoogleClient):
    google.events_by_id[GOOGLE_EVENT_ID] = google_event(recurringEventId="master")
    result = runner.invoke(app, ["draft-delete", "--event-id", GOOGLE_EVENT_ID])
    assert result.exit_code == 9 and "recurring series" in result.output
    assert google.write_calls == []
