"""Proves that no mutating Graph call happens unless the user types exactly ``yes``."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from outlook_calendar_agent.cli import app
from outlook_calendar_agent.errors import UserCancelledError

from .conftest import EVENT_ID, FakeGraphClient, graph_event, make_runtime

runner = CliRunner()

CREATE_ARGS = [
    "--subject",
    "Project review",
    "--start",
    "2026-10-07T14:00",
    "--duration",
    "45",
    "--location",
    "Room 4",
]


def _draft_id(output: str) -> str:
    match = re.search(r"Draft saved as (d-[0-9a-f]{6})", output)
    assert match, output
    return match.group(1)


# ---- draft-create / create -----------------------------------------------------------------
def test_draft_create_reads_conflicts_but_never_writes(rt, graph: FakeGraphClient, audit_entries):
    result = runner.invoke(app, ["draft-create", *CREATE_ARGS])
    assert result.exit_code == 0, result.output
    assert "PROPOSED CHANGE" in result.output
    assert "Start            : 2026-10-07 14:00 (Asia/Singapore)" in result.output
    assert "End              : 2026-10-07 14:45 (Asia/Singapore)" in result.output
    assert "Conflicts: 1 existing event(s)" in result.output
    assert "Nothing has been written" in result.output
    assert {c.method for c in graph.calls} == {"GET"}  # calendar list + calendarView only
    assert any(c.path.endswith("/calendarView") for c in graph.calls)
    assert [e["stage"] for e in audit_entries()] == ["proposed"]


def test_draft_create_without_conflict_check_makes_no_graph_call(rt, graph: FakeGraphClient):
    result = runner.invoke(app, ["draft-create", *CREATE_ARGS, "--no-conflict-check"])
    assert result.exit_code == 0, result.output
    assert graph.calls == []


def test_draft_create_with_attendees_requires_flag(rt, graph: FakeGraphClient):
    result = runner.invoke(app, ["draft-create", *CREATE_ARGS, "--attendee", "alice@example.com"])
    assert result.exit_code == 1
    assert "--send-invitations" in result.output
    assert graph.calls == []
    ok = runner.invoke(
        app,
        [
            "draft-create",
            *CREATE_ARGS,
            "--attendee",
            "Alice <alice@example.com>",
            "--send-invitations",
            "--no-conflict-check",
        ],
    )
    assert ok.exit_code == 0, ok.output
    assert "Invitations sent : YES" in ok.output


@pytest.mark.parametrize("answer", ["no\n", "ok\n", "yes yes\n", "y\n", "yes please\n", "\n", ""])
def test_create_does_not_post_without_exact_yes(rt, graph: FakeGraphClient, audit_entries, answer):
    draft = runner.invoke(app, ["draft-create", *CREATE_ARGS, "--no-conflict-check"])
    draft_id = _draft_id(draft.output)
    result = runner.invoke(app, ["create", "--draft", draft_id], input=answer)
    assert result.exit_code == UserCancelledError.exit_code, result.output
    assert "Create this event? Type yes to continue (anything else cancels):" in result.output
    assert "Cancelled. No changes were made." in result.output
    assert graph.write_calls == []
    assert [e["stage"] for e in audit_entries()] == ["proposed", "rejected"]
    # Draft survives a rejection so it can be retried.
    assert rt.drafts.load(draft_id).id == draft_id


def test_create_posts_exactly_once_after_yes(rt, graph: FakeGraphClient, audit_entries):
    draft = runner.invoke(app, ["draft-create", *CREATE_ARGS, "--no-conflict-check"])
    draft_id = _draft_id(draft.output)
    result = runner.invoke(app, ["create", "--draft", draft_id], input="yes\n")
    assert result.exit_code == 0, result.output
    posts = graph.write_calls
    assert len(posts) == 1 and posts[0].method == "POST" and posts[0].path == "/me/events"
    assert posts[0].json == {
        "subject": "Project review",
        "start": {"dateTime": "2026-10-07T14:00:00", "timeZone": "Asia/Singapore"},
        "end": {"dateTime": "2026-10-07T14:45:00", "timeZone": "Asia/Singapore"},
        "location": {"displayName": "Room 4"},
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 1440,
    }
    assert "Event ID: NEW-ID" in result.output
    stages = [e["stage"] for e in audit_entries()]
    assert stages == ["proposed", "confirmed", "succeeded"]
    assert audit_entries()[-1]["event_id"] == "NEW-ID"
    assert not (rt.settings.drafts_dir / f"{draft_id}.json").exists()


def test_create_inline_details_still_requires_yes(rt, graph: FakeGraphClient):
    result = runner.invoke(app, ["create", *CREATE_ARGS], input="no\n")
    assert result.exit_code == UserCancelledError.exit_code
    assert graph.write_calls == []
    result = runner.invoke(app, ["create", *CREATE_ARGS, "--no-conflict-check"], input="yes\n")
    assert result.exit_code == 0, result.output
    assert len(graph.write_calls) == 1


def test_create_failure_is_audited(rt, graph: FakeGraphClient, audit_entries):
    from outlook_calendar_agent.errors import PermissionDeniedError

    def deny(_payload):
        raise PermissionDeniedError(
            "Microsoft Graph denied the request (403 ErrorAccessDenied).",
            hint="writes need Calendars.ReadWrite",
        )

    graph.post_handler = deny
    result = runner.invoke(app, ["create", *CREATE_ARGS, "--no-conflict-check"], input="yes\n")
    assert result.exit_code == PermissionDeniedError.exit_code
    assert "Calendars.ReadWrite" in result.output
    assert [e["stage"] for e in audit_entries()] == ["proposed", "confirmed", "failed"]


def test_write_commands_refuse_in_read_only_configuration(tmp_path, graph, monkeypatch):
    from outlook_calendar_agent import runtime as runtime_module

    ro = make_runtime(tmp_path, graph, provider="microsoft", write=False)
    monkeypatch.setattr(runtime_module, "get_runtime", lambda: ro)
    result = runner.invoke(app, ["create", *CREATE_ARGS], input="yes\n")
    assert result.exit_code == 2
    assert "read-only" in result.output
    assert graph.write_calls == []


def test_audit_log_never_contains_token(rt, graph: FakeGraphClient):
    runner.invoke(app, ["create", *CREATE_ARGS, "--no-conflict-check"], input="yes\n")
    assert "not-a-real-token" not in rt.settings.audit_log_path.read_text()


# ---- draft-update / update -----------------------------------------------------------------
def test_draft_update_moves_event_keeping_duration(rt, graph: FakeGraphClient):
    result = runner.invoke(
        app, ["draft-update", "--event-id", EVENT_ID, "--start", "2026-10-07T16:00"]
    )
    assert result.exit_code == 0, result.output
    assert (
        "2026-10-07 14:00 (Asia/Singapore)  ->  2026-10-07 16:00 (Asia/Singapore)   [CHANGED]"
        in result.output
    )
    assert (
        "2026-10-07 15:00 (Asia/Singapore)  ->  2026-10-07 17:00 (Asia/Singapore)   [CHANGED]"
        in result.output
    )
    assert graph.write_calls == []


@pytest.mark.parametrize("answer", ["no\n", "nah\n", ""])
def test_update_does_not_patch_without_exact_yes(rt, graph: FakeGraphClient, audit_entries, answer):
    draft = runner.invoke(app, ["draft-update", "--event-id", EVENT_ID, "--subject", "Renamed"])
    draft_id = _draft_id(draft.output)
    result = runner.invoke(app, ["update", "--draft", draft_id], input=answer)
    assert result.exit_code == UserCancelledError.exit_code, result.output
    assert "Update this event? Type yes to continue (anything else cancels):" in result.output
    assert graph.write_calls == []
    assert [e["stage"] for e in audit_entries()] == ["proposed", "rejected"]


def test_update_patches_after_yes(rt, graph: FakeGraphClient, audit_entries):
    draft = runner.invoke(
        app, ["draft-update", "--event-id", EVENT_ID, "--start", "2026-10-07T16:00"]
    )
    draft_id = _draft_id(draft.output)
    result = runner.invoke(app, ["update", "--draft", draft_id], input="yes\n")
    assert result.exit_code == 0, result.output
    patches = graph.write_calls
    assert len(patches) == 1 and patches[0].method == "PATCH"
    assert patches[0].path == f"/me/events/{EVENT_ID.replace('=', '%3D')}"
    assert patches[0].json == {
        "start": {"dateTime": "2026-10-07T16:00:00", "timeZone": "Asia/Singapore"},
        "end": {"dateTime": "2026-10-07T17:00:00", "timeZone": "Asia/Singapore"},
    }
    assert [e["stage"] for e in audit_entries()] == ["proposed", "confirmed", "succeeded"]


def test_update_refuses_stale_draft(rt, graph: FakeGraphClient):
    draft = runner.invoke(app, ["draft-update", "--event-id", EVENT_ID, "--subject", "Renamed"])
    draft_id = _draft_id(draft.output)
    graph.events_by_id[EVENT_ID] = graph_event(change_key="ck-2")
    result = runner.invoke(app, ["update", "--draft", draft_id], input="yes\n")
    assert result.exit_code == 11
    assert "changed since the draft" in result.output
    assert graph.write_calls == []


def test_update_with_attendees_requires_notify_flag(rt, graph: FakeGraphClient):
    graph.events_by_id[EVENT_ID] = graph_event(
        attendees=[{"emailAddress": {"address": "a@example.com"}, "type": "required"}]
    )
    result = runner.invoke(app, ["draft-update", "--event-id", EVENT_ID, "--subject", "Renamed"])
    assert result.exit_code == 1 and "--notify-attendees" in result.output
    ok = runner.invoke(
        app, ["draft-update", "--event-id", EVENT_ID, "--subject", "Renamed", "--notify-attendees"]
    )
    assert ok.exit_code == 0, ok.output
    assert "Update notices sent : YES" in ok.output


def test_recurring_and_non_organizer_events_are_refused(rt, graph: FakeGraphClient):
    graph.events_by_id[EVENT_ID] = graph_event(event_type="seriesMaster")
    result = runner.invoke(app, ["draft-update", "--event-id", EVENT_ID, "--subject", "X"])
    assert result.exit_code == 9 and "recurring series" in result.output
    result = runner.invoke(app, ["draft-delete", "--event-id", EVENT_ID])
    assert result.exit_code == 9 and "recurring series" in result.output
    graph.events_by_id[EVENT_ID] = graph_event(is_organizer=False)
    result = runner.invoke(app, ["draft-update", "--event-id", EVENT_ID, "--subject", "X"])
    assert result.exit_code == 9 and "not the organizer" in result.output
    assert graph.write_calls == []


def test_find_resolves_or_lists_candidates(rt, graph: FakeGraphClient):
    graph.calendar_view = [
        graph_event(),
        graph_event(
            event_id="second",
            subject="Project review 2",
            start="2026-10-07T16:00:00",
            end="2026-10-07T17:00:00",
        ),
    ]
    graph.events_by_id["second"] = graph.calendar_view[1]
    ambiguous = runner.invoke(app, ["draft-delete", "--find", "project", "--on", "2026-10-07"])
    assert ambiguous.exit_code == 8
    assert "Multiple events match" in ambiguous.output and "id: second" in ambiguous.output
    none = runner.invoke(app, ["draft-delete", "--find", "dentist", "--on", "2026-10-07"])
    assert none.exit_code == 1 and "No events matching" in none.output
    unique = runner.invoke(app, ["draft-delete", "--find", "review 2", "--on", "2026-10-07"])
    assert unique.exit_code == 0, unique.output
    assert "Event ID           : second" in unique.output
    assert graph.write_calls == []


# ---- draft-delete / delete -----------------------------------------------------------------
def test_delete_preview_includes_id_subject_and_singapore_times(rt, graph: FakeGraphClient):
    draft = runner.invoke(app, ["draft-delete", "--event-id", EVENT_ID])
    assert draft.exit_code == 0, draft.output
    draft_id = _draft_id(draft.output)
    result = runner.invoke(app, ["delete", "--draft", draft_id], input="no\n")
    assert result.exit_code == UserCancelledError.exit_code
    assert "PERMANENT DELETE" in result.output
    assert f"Event ID : {EVENT_ID}" in result.output
    assert "Subject  : Project review" in result.output
    assert "Start    : 2026-10-07 14:00 (Asia/Singapore)" in result.output
    assert "End      : 2026-10-07 15:00 (Asia/Singapore)" in result.output
    assert (
        "Delete this event permanently? Type yes to continue (anything else cancels):"
        in result.output
    )
    assert graph.write_calls == []


@pytest.mark.parametrize("answer", ["yup\n", "y\n", "delete\n", ""])
def test_delete_does_not_call_graph_without_exact_yes(
    rt, graph: FakeGraphClient, audit_entries, answer
):
    draft_id = _draft_id(runner.invoke(app, ["draft-delete", "--event-id", EVENT_ID]).output)
    result = runner.invoke(app, ["delete", "--draft", draft_id], input=answer)
    assert result.exit_code == UserCancelledError.exit_code
    assert graph.write_calls == []
    assert [e["stage"] for e in audit_entries()] == ["proposed", "rejected"]


def test_delete_after_yes(rt, graph: FakeGraphClient, audit_entries):
    draft_id = _draft_id(runner.invoke(app, ["draft-delete", "--event-id", EVENT_ID]).output)
    result = runner.invoke(app, ["delete", "--draft", draft_id], input="yes\n")
    assert result.exit_code == 0, result.output
    deletes = graph.write_calls
    assert len(deletes) == 1 and deletes[0].method == "DELETE"
    assert deletes[0].path == f"/me/events/{EVENT_ID.replace('=', '%3D')}"
    assert [e["stage"] for e in audit_entries()] == ["proposed", "confirmed", "succeeded"]
    assert audit_entries()[-1]["event_id"] == EVENT_ID


def test_wrong_draft_kind_and_discard(rt, graph: FakeGraphClient):
    draft_id = _draft_id(runner.invoke(app, ["draft-delete", "--event-id", EVENT_ID]).output)
    result = runner.invoke(app, ["update", "--draft", draft_id], input="yes\n")
    assert result.exit_code == 1 and "is a 'delete' draft" in result.output
    assert graph.write_calls == []
    assert runner.invoke(app, ["discard", "--draft", draft_id]).exit_code == 0
    assert runner.invoke(app, ["drafts"]).output.strip() == "No saved drafts."
