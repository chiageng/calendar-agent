"""Write commands: draft-create/create, draft-update/update, draft-delete/delete.

The ``draft-*`` commands never call a mutating API endpoint. The ``create``/``update``/``delete``
commands re-print the preview and mutate only after the user types exactly ``yes``.
"""

from __future__ import annotations

from typing import Annotated

import typer

from . import runtime
from .calendars import find_conflicts_everywhere, resolve_calendar
from .errors import AgentError
from .formatting import (
    format_conflicts,
    format_create_preview,
    format_delete_preview,
    format_update_preview,
)
from .models import Attendee, CalendarInfo, Draft, EventDraft
from .runtime import Runtime
from .timeutil import DATETIME_HELP, format_dt
from .write_flow import (
    build_delete_draft,
    build_update_draft,
    check_notification_policy,
    confirm_and_execute,
    ensure_write_allowed,
    parse_event_times,
    resolve_event,
    summary_for_create,
    summary_for_delete,
    summary_for_update,
    verify_unchanged,
)

# ---- shared option types -----------------------------------------------------------------
SubjectOpt = Annotated[str | None, typer.Option("--subject", help="Event subject.")]
StartOpt = Annotated[str | None, typer.Option("--start", help=f"Start. {DATETIME_HELP}")]
EndOpt = Annotated[str | None, typer.Option("--end", help="End (same formats as --start).")]
DurationOpt = Annotated[
    int | None, typer.Option("--duration", min=1, max=24 * 60, help="Duration in minutes.")
]
LocationOpt = Annotated[str | None, typer.Option("--location", help="Location text.")]
BodyOpt = Annotated[str | None, typer.Option("--body", help="Plain-text description.")]
AttendeeOpt = Annotated[
    list[str] | None,
    typer.Option("--attendee", help="Required attendee: email or 'Name <email>'. Repeatable."),
]
OptionalAttendeeOpt = Annotated[
    list[str] | None, typer.Option("--optional-attendee", help="Optional attendee. Repeatable.")
]
SendInvitesOpt = Annotated[
    bool,
    typer.Option(
        "--send-invitations",
        help="E-mail invitations to attendees (otherwise they are added silently where the "
        "provider allows it).",
    ),
]
NotifyOpt = Annotated[
    bool,
    typer.Option(
        "--notify-attendees",
        help="E-mail existing attendees about this change.",
    ),
]
NoConflictCheckOpt = Annotated[
    bool, typer.Option("--no-conflict-check", help="Skip the calendarView conflict read.")
]
DraftOpt = Annotated[str | None, typer.Option("--draft", help="Saved draft ID (d-xxxxxx).")]
CalendarOpt = Annotated[
    str | None,
    typer.Option("--calendar", help="Calendar name or ID (default: primary). See 'calendars'."),
]
EventIdOpt = Annotated[
    str | None, typer.Option("--event-id", help="Exact event ID from the provider.")
]
FindOpt = Annotated[
    str | None, typer.Option("--find", help="Subject text to search for (case-insensitive).")
]
OnOpt = Annotated[str | None, typer.Option("--on", help="Limit --find to this date.")]
DaysOpt = Annotated[
    int, typer.Option("--days", min=1, max=90, help="Search window for --find when --on is absent.")
]


def _echo(message: str = "") -> None:
    typer.echo(message)


def _handle(func):  # type: ignore[no-untyped-def]
    from .cli import handle_errors

    return handle_errors(func)


# ---- helpers ------------------------------------------------------------------------------
def _parse_attendees(required: list[str] | None, optional: list[str] | None) -> list[Attendee]:
    attendees: list[Attendee] = []
    try:
        attendees += [Attendee.parse(a, type="required") for a in required or []]
        attendees += [Attendee.parse(a, type="optional") for a in optional or []]
    except ValueError as exc:
        raise AgentError(f"Invalid attendee: {exc}") from exc
    seen: set[str] = set()
    unique: list[Attendee] = []
    for attendee in attendees:
        key = attendee.email.lower()
        if key not in seen:
            seen.add(key)
            unique.append(attendee)
    return unique


def _build_create_draft(
    rt: Runtime,
    *,
    subject: str | None,
    start: str | None,
    end: str | None,
    duration: int | None,
    location: str | None,
    body: str | None,
    attendee: list[str] | None,
    optional_attendee: list[str] | None,
    send_invitations: bool,
    calendar: str | None = None,
) -> EventDraft:
    if not subject or not start:
        raise AgentError("--subject and --start are required (or pass --draft <ID>).")
    start_dt, end_dt = parse_event_times(
        rt, start_text=start, end_text=end, duration_minutes=duration
    )
    attendees = _parse_attendees(attendee, optional_attendee)
    check_notification_policy(
        rt, has_attendees=bool(attendees), acknowledged=send_invitations, flag="--send-invitations"
    )
    target = resolve_calendar(rt.calendar, calendar, for_write=True)  # network: after validation
    try:
        return EventDraft(
            subject=subject.strip(),
            start=start_dt,
            end=end_dt,
            location=location,
            body=body,
            attendees=attendees,
            send_invitations=send_invitations,
            calendar_id=target.id,
            calendar_name=target.name,
        )
    except ValueError as exc:
        from .write_flow import _first_pydantic_message

        raise AgentError(_first_pydantic_message(exc)) from exc


def _show_create(rt: Runtime, draft: EventDraft, *, check_conflicts: bool) -> None:
    _echo(format_create_preview(draft, rt.tz))
    if check_conflicts:
        target = CalendarInfo(id=draft.calendar_id, name=draft.calendar_name or draft.calendar_id)
        conflicts = find_conflicts_everywhere(rt.calendar, draft.start, draft.end, target=target)
        _echo(format_conflicts(conflicts, rt.tz))
    else:
        _echo("Conflicts: not checked (--no-conflict-check).")


def _load_draft(rt: Runtime, draft_id: str, expected_kind: str) -> Draft:
    draft = rt.drafts.load(draft_id)
    if draft.kind != expected_kind:
        raise AgentError(
            f"Draft {draft_id} is a '{draft.kind}' draft, not '{expected_kind}'.",
            hint=f"Use the '{draft.kind}' command for it.",
        )
    return draft


def _saved_notice(draft: Draft, command: str) -> str:
    return (
        f"\nDraft saved as {draft.id}. Nothing has been written to your calendar.\n"
        f"To apply it: uv run outlook-calendar {command} --draft {draft.id}"
    )


# ---- commands -----------------------------------------------------------------------------
def register(app: typer.Typer) -> None:
    app.command(name="draft-create")(draft_create)
    app.command(name="create")(create)
    app.command(name="draft-update")(draft_update)
    app.command(name="update")(update)
    app.command(name="draft-delete")(draft_delete)
    app.command(name="delete")(delete)
    app.command(name="discard")(discard)


@_handle
def draft_create(
    subject: SubjectOpt = None,
    start: StartOpt = None,
    end: EndOpt = None,
    duration: DurationOpt = None,
    location: LocationOpt = None,
    body: BodyOpt = None,
    attendee: AttendeeOpt = None,
    optional_attendee: OptionalAttendeeOpt = None,
    send_invitations: SendInvitesOpt = False,
    no_conflict_check: NoConflictCheckOpt = False,
    calendar: CalendarOpt = None,
) -> None:
    """Prepare a new event, check conflicts (read-only) and save it as a draft. No write occurs."""
    rt = runtime.get_runtime()
    draft = _build_create_draft(
        rt,
        subject=subject,
        start=start,
        end=end,
        duration=duration,
        location=location,
        body=body,
        attendee=attendee,
        optional_attendee=optional_attendee,
        send_invitations=send_invitations,
        calendar=calendar,
    )
    _show_create(rt, draft, check_conflicts=not no_conflict_check)
    saved = rt.drafts.save(draft)
    s = summary_for_create(draft)
    rt.audit.record(
        action="create",
        stage="proposed",
        draft_id=saved.id,
        subject=s.subject,
        start=s.start,
        end=s.end,
    )
    _echo(_saved_notice(saved, "create"))


@_handle
def create(
    draft_id: DraftOpt = None,
    subject: SubjectOpt = None,
    start: StartOpt = None,
    end: EndOpt = None,
    duration: DurationOpt = None,
    location: LocationOpt = None,
    body: BodyOpt = None,
    attendee: AttendeeOpt = None,
    optional_attendee: OptionalAttendeeOpt = None,
    send_invitations: SendInvitesOpt = False,
    no_conflict_check: NoConflictCheckOpt = False,
    calendar: CalendarOpt = None,
) -> None:
    """Create an event after you type exactly 'yes'."""
    rt = runtime.get_runtime()
    ensure_write_allowed(rt)
    inline = any(
        [subject, start, end, duration, location, body, attendee, optional_attendee, calendar]
    )
    if draft_id and inline:
        raise AgentError("Pass either --draft or inline event details, not both.")
    if draft_id:
        draft = _load_draft(rt, draft_id, "create").payload
        assert isinstance(draft, EventDraft)
    else:
        draft = _build_create_draft(
            rt,
            subject=subject,
            start=start,
            end=end,
            duration=duration,
            location=location,
            body=body,
            attendee=attendee,
            optional_attendee=optional_attendee,
            send_invitations=send_invitations,
            calendar=calendar,
        )
    s = summary_for_create(draft)
    if not draft_id:
        rt.audit.record(
            action="create", stage="proposed", subject=s.subject, start=s.start, end=s.end
        )
    _show_create(rt, draft, check_conflicts=not no_conflict_check)
    _echo()
    created = confirm_and_execute(
        rt,
        summary=s,
        draft_id=draft_id,
        question="Create this event?",
        perform=lambda: rt.calendar.create_event(draft),
        echo=_echo,
    )
    if draft_id:
        rt.drafts.discard(draft_id)
    _echo(f"Created: {created.subject}  {format_dt(created.start, rt.tz)}")
    _echo(f"Event ID: {created.id}")


@_handle
def draft_update(
    event_id: EventIdOpt = None,
    find: FindOpt = None,
    on: OnOpt = None,
    days: DaysOpt = 7,
    subject: SubjectOpt = None,
    start: StartOpt = None,
    end: EndOpt = None,
    duration: DurationOpt = None,
    location: LocationOpt = None,
    notify_attendees: NotifyOpt = False,
    no_conflict_check: NoConflictCheckOpt = False,
    calendar: CalendarOpt = None,
) -> None:
    """Resolve one event, prepare changes, check conflicts and save a draft. No write occurs."""
    rt = runtime.get_runtime()
    target = resolve_calendar(rt.calendar, calendar, for_write=True)
    event = resolve_event(rt, event_id=event_id, find=find, on=on, days=days, calendar_id=target.id)
    draft = build_update_draft(
        rt,
        event,
        subject=subject,
        start_text=start,
        end_text=end,
        duration_minutes=duration,
        location=location,
        notify_attendees=notify_attendees,
        calendar_name=target.name,
    )
    _echo(format_update_preview(draft, rt.tz))
    if no_conflict_check:
        _echo("Conflicts: not checked (--no-conflict-check).")
    else:
        conflicts = find_conflicts_everywhere(
            rt.calendar,
            draft.effective_start,
            draft.effective_end,
            exclude_id=event.id,
            target=target,
        )
        _echo(format_conflicts(conflicts, rt.tz))
    saved = rt.drafts.save(draft)
    s = summary_for_update(draft)
    rt.audit.record(
        action="update",
        stage="proposed",
        draft_id=saved.id,
        event_id=s.event_id,
        subject=s.subject,
        start=s.start,
        end=s.end,
    )
    _echo(_saved_notice(saved, "update"))


@_handle
def update(
    draft_id: Annotated[str, typer.Option("--draft", help="Saved update draft ID.")],
) -> None:
    """Apply a saved update draft after you type exactly 'yes'."""
    rt = runtime.get_runtime()
    ensure_write_allowed(rt)
    draft = _load_draft(rt, draft_id, "update").payload
    assert draft.kind == "update"
    verify_unchanged(rt, draft.original)
    _echo(format_update_preview(draft, rt.tz))
    conflicts = find_conflicts_everywhere(
        rt.calendar,
        draft.effective_start,
        draft.effective_end,
        exclude_id=draft.original.id,
        target=CalendarInfo(
            id=draft.original.calendar_id,
            name=draft.original.calendar_name or draft.original.calendar_id,
        ),
    )
    _echo(format_conflicts(conflicts, rt.tz))
    _echo()
    updated = confirm_and_execute(
        rt,
        summary=summary_for_update(draft),
        draft_id=draft_id,
        question="Update this event?",
        perform=lambda: rt.calendar.update_event(draft),
        echo=_echo,
    )
    rt.drafts.discard(draft_id)
    _echo(
        f"Updated: {updated.subject}  {format_dt(updated.start, rt.tz)}–"
        f"{updated.end.astimezone(rt.tz):%H:%M}"
    )


@_handle
def draft_delete(
    event_id: EventIdOpt = None,
    find: FindOpt = None,
    on: OnOpt = None,
    days: DaysOpt = 7,
    notify_attendees: NotifyOpt = False,
    calendar: CalendarOpt = None,
) -> None:
    """Resolve one event and save a deletion draft. No write occurs."""
    rt = runtime.get_runtime()
    target = resolve_calendar(rt.calendar, calendar, for_write=True)
    event = resolve_event(rt, event_id=event_id, find=find, on=on, days=days, calendar_id=target.id)
    draft = build_delete_draft(
        rt, event, notify_attendees=notify_attendees, calendar_name=target.name
    )
    _echo(format_delete_preview(draft, rt.tz))
    saved = rt.drafts.save(draft)
    s = summary_for_delete(draft)
    rt.audit.record(
        action="delete",
        stage="proposed",
        draft_id=saved.id,
        event_id=s.event_id,
        subject=s.subject,
        start=s.start,
        end=s.end,
    )
    _echo(_saved_notice(saved, "delete"))


@_handle
def delete(
    draft_id: Annotated[str, typer.Option("--draft", help="Saved delete draft ID.")],
) -> None:
    """Permanently delete an event after you type exactly 'yes'."""
    rt = runtime.get_runtime()
    ensure_write_allowed(rt)
    draft = _load_draft(rt, draft_id, "delete").payload
    assert draft.kind == "delete"
    verify_unchanged(rt, draft.original)
    o = draft.original
    _echo(format_delete_preview(draft, rt.tz))
    _echo()
    _echo("!!! PERMANENT DELETE — this cannot be undone !!!")
    _echo(f"  Event ID : {o.id}")
    _echo(f"  Subject  : {o.subject}")
    _echo(f"  Start    : {format_dt(o.start, rt.tz)}")
    _echo(f"  End      : {format_dt(o.end, rt.tz)}")
    _echo()
    confirm_and_execute(
        rt,
        summary=summary_for_delete(draft),
        draft_id=draft_id,
        question="Delete this event permanently?",
        perform=lambda: rt.calendar.delete_event(draft),
        echo=_echo,
    )
    rt.drafts.discard(draft_id)
    _echo(f"Deleted: {o.subject}  {format_dt(o.start, rt.tz)}")


@_handle
def discard(draft_id: Annotated[str, typer.Option("--draft", help="Draft ID to discard.")]) -> None:
    """Discard a saved draft locally. Never touches the calendar provider."""
    rt = runtime.get_runtime()
    rt.drafts.load(draft_id)  # validates existence
    rt.drafts.discard(draft_id)
    _echo(f"Discarded draft {draft_id}.")
