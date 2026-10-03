"""Terminal rendering. All times are shown in the configured display zone (Asia/Singapore)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, tzinfo
from typing import Any

from .models import (
    Attendee,
    CalendarEvent,
    CalendarInfo,
    DeleteDraft,
    EventDraft,
    TaskItem,
    UpdateDraft,
)
from .timeutil import SGT, format_dt, zone_name

_NONE = "(none)"


def format_signed_in(me: dict[str, Any]) -> str:
    name = me.get("displayName") or "(unknown)"
    email = me.get("mail") or me.get("userPrincipalName") or "(no e-mail)"
    return f"Signed in as: {name} <{email}>"


def format_event_line(event: CalendarEvent, tz: tzinfo = SGT) -> str:
    start, end = event.start.astimezone(tz), event.end.astimezone(tz)
    if event.is_all_day:
        when = "all day     "
    elif start.date() != end.date():
        when = f"{start:%H:%M}–{end:%m-%d %H:%M}"
    else:
        when = f"{start:%H:%M}–{end:%H:%M}"
    parts = [event.subject]
    if event.is_cancelled:
        parts[0] = f"[cancelled] {parts[0]}"
    if event.location:
        parts.append(event.location)
    if event.organizer_email:
        parts.append(event.organizer_email)
    if event.event_type and event.event_type != "singleInstance":
        parts.append("recurring")
    return f"  {when}  {' | '.join(parts)}"


def format_tasks(
    tasks: Sequence[TaskItem], start: datetime, end: datetime, tz: tzinfo = SGT
) -> str:
    header = (
        f"Tasks due {start.astimezone(tz):%Y-%m-%d} to {end.astimezone(tz):%Y-%m-%d}"
        " (Google Tasks; dates only)"
    )
    if not tasks:
        return f"{header}\n(no tasks)"
    lines = [header]
    current = None
    for task in tasks:
        label = task.due.isoformat() if task.due else "no due date"
        if label != current:
            lines.append(label)
            current = label
        mark = "[x]" if task.completed else "[ ]"
        extra = f" | {task.list_name}" if task.list_name != "My Tasks" else ""
        lines.append(f"  {mark} {task.title}{extra}")
    return "\n".join(lines)


def format_calendars(calendars: Sequence[CalendarInfo]) -> str:
    if not calendars:
        return "No calendars visible."
    return "\n".join(c.display() for c in calendars)


def format_event_list(
    events: Sequence[CalendarEvent],
    start: datetime,
    end: datetime,
    tz: tzinfo = SGT,
    *,
    calendar_id: str = "primary",
    calendar_name: str | None = None,
) -> str:
    header = (
        f"Events {start.astimezone(tz):%Y-%m-%d %H:%M} to {end.astimezone(tz):%Y-%m-%d %H:%M}"
        f" — {zone_name(tz)}"
    )
    if calendar_id != "primary":
        label = f"{calendar_name} ({calendar_id})" if calendar_name else calendar_id
        header += f"\nCalendar: {label}"
    if not events:
        return f"{header}\n(no events)"
    lines = [header]
    current_day = None
    for event in events:
        day = event.start.astimezone(tz).date()
        if day != current_day:
            lines.append(f"{day:%Y-%m-%d}")
            current_day = day
        lines.append(format_event_line(event, tz))
    return "\n".join(lines)


def format_event_choices(events: Sequence[CalendarEvent], tz: tzinfo = SGT) -> str:
    lines = ["Multiple events match. Re-run with --event-id <ID> for one of:"]
    for index, event in enumerate(events, start=1):
        lines.append(f"  {index}. {format_dt(event.start, tz)}  {event.subject}")
        lines.append(f"     id: {event.id}")
    return "\n".join(lines)


def _attendees(attendees: Iterable[Attendee]) -> str:
    items = [a.display() for a in attendees]
    return "; ".join(items) if items else _NONE


def _notification_status(has_attendees: bool, acknowledged: bool) -> str:
    if not has_attendees:
        return "no (no attendees)"
    if acknowledged:
        return "YES (all attendees will be e-mailed)"
    return "no (attendees are changed on the event without an e-mail)"


def _block(title: str, rows: list[tuple[str, str]]) -> str:
    width = max(len(label) for label, _ in rows)
    lines = [title, "-" * len(title)]
    lines += [f"{label.ljust(width)} : {value}" for label, value in rows]
    return "\n".join(lines)


def format_conflicts(conflicts: Sequence[CalendarEvent], tz: tzinfo = SGT) -> str:
    if not conflicts:
        return "Conflicts: none in the proposed window."
    lines = [f"Conflicts: {len(conflicts)} existing event(s) overlap the proposed time:"]
    lines += [format_event_line(e, tz) for e in conflicts]
    return "\n".join(lines)


def format_create_preview(draft: EventDraft, tz: tzinfo = SGT) -> str:
    rows = [
        ("Action", "CREATE event"),
        ("Event ID", "(assigned by the calendar provider on creation)"),
        ("Subject", draft.subject),
        ("Start", format_dt(draft.start, tz)),
        ("End", format_dt(draft.end, tz)),
        ("Calendar", draft.calendar_label),
        ("Location", draft.location or _NONE),
        ("Meeting link", "(none — Meet/Teams links are not created by this tool)"),
        ("Attendees", _attendees(draft.attendees)),
        ("Invitations sent", _notification_status(bool(draft.attendees), draft.send_invitations)),
        ("Recurrence", "none (single event)"),
    ]
    if draft.body:
        rows.append(("Body", draft.body))
    return _block("PROPOSED CHANGE", rows)


def format_update_preview(draft: UpdateDraft, tz: tzinfo = SGT) -> str:
    original = draft.original

    def change(label: str, old: str, new: str | None) -> tuple[str, str]:
        if new is None or new == old:
            return (label, old)
        return (label, f"{old}  ->  {new}   [CHANGED]")

    changed = set(draft.changed_fields())
    rows = [
        (
            "Action",
            f"UPDATE event (PATCH /me/events/{{id}}) — fields: {', '.join(sorted(changed))}",
        ),
        ("Event ID", original.id),
        change("Subject", original.subject, draft.subject if "subject" in changed else None),
        change(
            "Start",
            format_dt(original.start, tz),
            format_dt(draft.effective_start, tz) if "start" in changed else None,
        ),
        change(
            "End",
            format_dt(original.end, tz),
            format_dt(draft.effective_end, tz) if "end" in changed else None,
        ),
        ("Calendar", original.calendar_label),
        change(
            "Location",
            original.location or _NONE,
            draft.location if "location" in changed else None,
        ),
        ("Meeting link", original.online_join_url or _NONE),
        ("Attendees", _attendees(original.attendees)),
        (
            "Update notices sent",
            _notification_status(bool(original.attendees), draft.notify_attendees),
        ),
        ("Recurrence", "none (single event)"),
    ]
    return _block("PROPOSED CHANGE", rows)


def format_delete_preview(draft: DeleteDraft, tz: tzinfo = SGT) -> str:
    original = draft.original
    rows = [
        ("Action", "DELETE event — PERMANENT"),
        ("Event ID", original.id),
        ("Subject", original.subject),
        ("Start", format_dt(original.start, tz)),
        ("End", format_dt(original.end, tz)),
        ("Calendar", original.calendar_label),
        ("Location", original.location or _NONE),
        ("Meeting link", original.online_join_url or _NONE),
        ("Attendees", _attendees(original.attendees)),
        (
            "Cancellations sent",
            _notification_status(bool(original.attendees), draft.notify_attendees),
        ),
        (
            "Your role",
            "organizer"
            if original.is_organizer
            else "attendee (removes it from your calendar only)",
        ),
    ]
    return _block("PROPOSED DELETION", rows)
