"""Shared logic for the write commands: event resolution, draft construction and the
confirmation gate. Every calendar mutation in this project passes through ``confirm_and_execute``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .audit import Action
from .confirm import CONFIRMATION_WORD, ask_confirmation
from .errors import (
    AgentError,
    AmbiguousEventError,
    ConfigError,
    StaleDraftError,
    UnsupportedOperationError,
    UserCancelledError,
)
from .formatting import format_event_choices
from .models import Attendee, CalendarEvent, DeleteDraft, EventDraft, EventSnapshot, UpdateDraft
from .runtime import Runtime
from .timeutil import parse_user_datetime, start_of_day

DEFAULT_DURATION_MINUTES = 30


@dataclass(frozen=True)
class WriteSummary:
    """The fields recorded in the audit log for a mutation."""

    action: Action
    event_id: str | None
    subject: str
    start: datetime
    end: datetime
    calendar_id: str = "primary"


def ensure_write_allowed(rt: Runtime) -> None:
    if not rt.settings.can_write:
        raise ConfigError(
            "This configuration is read-only: the configured scopes do not allow calendar writes.",
            hint=rt.settings.write_scope_hint,
        )


def check_notification_policy(
    rt: Runtime, *, has_attendees: bool, acknowledged: bool, flag: str
) -> None:
    """Providers that cannot suppress attendee e-mails require an explicit acknowledgement."""
    if has_attendees and not acknowledged and not rt.calendar.can_suppress_notifications:
        raise AgentError(
            f"{rt.calendar.name} always e-mails attendees about this change. Pass {flag} to "
            "acknowledge that, or remove the attendees.",
        )


def ensure_single_instance(event: CalendarEvent, *, verb: str) -> None:
    if event.event_type and event.event_type != "singleInstance":
        raise UnsupportedOperationError(
            f"Cannot {verb} event {event.id!r}: it belongs to a recurring series "
            f"(type={event.event_type}).",
            hint="Modifying or deleting recurring-series data is deferred. Handle recurring "
            "events in Outlook directly for now.",
        )


def ensure_organizer(event: CalendarEvent) -> None:
    if event.is_organizer is False:
        raise UnsupportedOperationError(
            "You are not the organizer of this event, so it cannot be updated from here.",
            hint="Only the organizer can change an event; changing the organizer is deferred.",
        )


def parse_event_times(
    rt: Runtime, *, start_text: str, end_text: str | None, duration_minutes: int | None
) -> tuple[datetime, datetime]:
    if end_text and duration_minutes:
        raise AgentError("Pass either --end or --duration, not both.")
    try:
        start = parse_user_datetime(start_text, tz=rt.tz)
        if end_text:
            end = parse_user_datetime(end_text, tz=rt.tz)
        else:
            end = start + timedelta(minutes=duration_minutes or DEFAULT_DURATION_MINUTES)
    except ValueError as exc:
        raise AgentError(str(exc)) from exc
    if end <= start:
        raise AgentError("The end time must be after the start time.")
    return start, end


def resolve_event(
    rt: Runtime,
    *,
    event_id: str | None,
    find: str | None,
    on: str | None,
    days: int,
    calendar_id: str = "primary",
    window: tuple[datetime, datetime] | None = None,
) -> CalendarEvent:
    """Resolve exactly one event, or raise with a list of candidates.

    ``window`` (start, end) overrides ``on``/``days`` when the caller already resolved a range.
    """
    if event_id and find:
        raise AgentError("Pass either --event-id or --find, not both.")
    if event_id:
        return rt.calendar.get_event(event_id, calendar_id=calendar_id)
    if not find:
        raise AgentError(
            "Choose an event with --event-id <ID> or --find <text> [--on <date> | --days N]."
        )
    try:
        if window is not None:
            window_start, window_end = window
        elif on:
            window_start = start_of_day(parse_user_datetime(on, tz=rt.tz), rt.tz)
            window_end = window_start + timedelta(days=1)
        else:
            window_start = start_of_day(datetime.now(rt.tz), rt.tz)
            window_end = window_start + timedelta(days=days)
    except ValueError as exc:
        raise AgentError(str(exc)) from exc

    matches = rt.calendar.search_events(window_start, window_end, find, calendar_id=calendar_id)
    if not matches:
        raise AgentError(
            f"No events matching {find!r} between {window_start:%Y-%m-%d} and "
            f"{window_end:%Y-%m-%d}.",
            hint="Widen the search with --days, pick another --on date, or pass --event-id.",
        )
    if len(matches) > 1:
        raise AmbiguousEventError(format_event_choices(matches, rt.tz))
    return rt.calendar.get_event(matches[0].id, calendar_id=calendar_id)


def build_update_draft(
    rt: Runtime,
    event: CalendarEvent,
    *,
    subject: str | None,
    start_text: str | None,
    end_text: str | None,
    duration_minutes: int | None,
    location: str | None,
    notify_attendees: bool,
    calendar_name: str | None = None,
    link: str | None = None,
    reminder_minutes_before: int | None = None,
    use_default_reminder: bool = False,
) -> UpdateDraft:
    ensure_single_instance(event, verb="update")
    ensure_organizer(event)
    check_notification_policy(
        rt,
        has_attendees=bool(event.attendees),
        acknowledged=notify_attendees,
        flag="--notify-attendees",
    )
    if end_text and duration_minutes:
        raise AgentError("Pass either --end or --duration, not both.")
    try:
        new_start = parse_user_datetime(start_text, tz=rt.tz) if start_text else None
        new_end = parse_user_datetime(end_text, tz=rt.tz) if end_text else None
    except ValueError as exc:
        raise AgentError(str(exc)) from exc
    if duration_minutes:
        new_end = (new_start or event.start) + timedelta(minutes=duration_minutes)
    elif new_start is not None and new_end is None:
        new_end = new_start + (event.end - event.start)  # keep the original duration
    try:
        return UpdateDraft(
            original=EventSnapshot.from_event(event, calendar_name=calendar_name),
            subject=subject,
            start=new_start,
            end=new_end,
            location=location,
            link=link,
            reminder_minutes_before=reminder_minutes_before,
            use_default_reminder=use_default_reminder,
            notify_attendees=notify_attendees,
        )
    except ValueError as exc:
        raise AgentError(_first_pydantic_message(exc)) from exc


def build_delete_draft(
    rt: Runtime,
    event: CalendarEvent,
    *,
    notify_attendees: bool,
    calendar_name: str | None = None,
) -> DeleteDraft:
    ensure_single_instance(event, verb="delete")
    check_notification_policy(
        rt,
        has_attendees=bool(event.attendees),
        acknowledged=notify_attendees,
        flag="--notify-attendees",
    )
    return DeleteDraft(
        original=EventSnapshot.from_event(event, calendar_name=calendar_name),
        notify_attendees=notify_attendees,
    )


def verify_unchanged(rt: Runtime, snapshot: EventSnapshot) -> CalendarEvent:
    """Re-read the target event and refuse to proceed if it changed since the draft."""
    current = rt.calendar.get_event(snapshot.id, calendar_id=snapshot.calendar_id)
    if not snapshot.matches(current):
        raise StaleDraftError(
            f"Event {snapshot.id!r} changed since the draft was prepared.",
            hint="Discard this draft and prepare a new one from the current event.",
        )
    return current


def summary_for_create(draft: EventDraft) -> WriteSummary:
    return WriteSummary(
        "create", None, draft.subject, draft.start, draft.end, calendar_id=draft.calendar_id
    )


def summary_for_update(draft: UpdateDraft) -> WriteSummary:
    return WriteSummary(
        "update",
        draft.original.id,
        draft.subject or draft.original.subject,
        draft.effective_start,
        draft.effective_end,
        calendar_id=draft.original.calendar_id,
    )


def summary_for_delete(draft: DeleteDraft) -> WriteSummary:
    o = draft.original
    return WriteSummary("delete", o.id, o.subject, o.start, o.end, calendar_id=o.calendar_id)


def confirm_and_execute[T](
    rt: Runtime,
    *,
    summary: WriteSummary,
    draft_id: str | None,
    question: str,
    perform: Callable[[], T],
    echo: Callable[[str], None],
    reader: Callable[[str], str] = input,
) -> T:
    """Ask for the exact confirmation word, audit the decision and only then call ``perform``."""
    prompt = f"{question} Type {CONFIRMATION_WORD} to continue (anything else cancels): "
    fields = {
        "draft_id": draft_id,
        "event_id": summary.event_id,
        "subject": summary.subject,
        "start": summary.start,
        "end": summary.end,
        "extra": {"calendar_id": summary.calendar_id},
    }
    if not ask_confirmation(prompt, reader=reader):
        rt.audit.record(action=summary.action, stage="rejected", **fields)
        echo("Cancelled. No changes were made.")
        raise UserCancelledError(f"{summary.action} was not confirmed.")
    rt.audit.record(action=summary.action, stage="confirmed", **fields)
    try:
        result = perform()
    except AgentError as exc:
        rt.audit.record(action=summary.action, stage="failed", detail=exc.message, **fields)
        raise
    if summary.event_id is None and isinstance(result, CalendarEvent):
        fields["event_id"] = result.id
    rt.audit.record(action=summary.action, stage="succeeded", **fields)
    return result


def parse_attendees(
    required: list[str] | None, optional: list[str] | None = None
) -> list[Attendee]:
    """Parse 'email' / 'Name <email>' strings, de-duplicated case-insensitively by address."""
    attendees: list[Attendee] = []
    try:
        attendees += [Attendee.parse(a, type="required") for a in required or []]
        attendees += [Attendee.parse(a, type="optional") for a in optional or []]
    except ValueError as exc:
        raise AgentError(
            f"Invalid attendee: {exc}. Attendees must be e-mail addresses; names cannot be "
            "looked up."
        ) from exc
    seen: set[str] = set()
    unique: list[Attendee] = []
    for attendee in attendees:
        key = attendee.email.lower()
        if key not in seen:
            seen.add(key)
            unique.append(attendee)
    return unique


def first_pydantic_message(exc: ValueError) -> str:
    """The first human-readable message out of a pydantic ValidationError (or any ValueError)."""
    return _first_pydantic_message(exc)


def _first_pydantic_message(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        details = errors()
        if details:
            return str(details[0].get("msg", exc)).removeprefix("Value error, ")
    return str(exc)
