"""Programmatic API used by the MCP server (and usable by any other front end).

Every function takes the Runtime and plain arguments, returns human-readable text, and never
raises for expected conditions: ambiguities come back as ``QUESTION: ...``, errors as
``ERROR: ...``. Mutations still go through ``write_flow.confirm_and_execute`` and only happen in
``confirm_draft`` when the caller passes the user's literal reply, the single word ``yes``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta

from .calendars import find_conflicts_everywhere, resolve_calendar
from .confirm import is_exact_confirmation
from .dates import DateAmbiguity, resolve_moment, resolve_time, resolve_window
from .errors import AgentError, UserCancelledError
from .formatting import (
    format_calendars,
    format_conflicts,
    format_create_preview,
    format_delete_preview,
    format_draft_line,
    format_event_list,
    format_tasks,
    format_update_preview,
)
from .models import (
    DEFAULT_REMINDER_MINUTES,
    REMINDER_OFF,
    DeleteDraft,
    EventDraft,
    UpdateDraft,
)
from .runtime import Runtime
from .timeutil import format_dt
from .titles import clean_subject_and_location
from .write_flow import (
    build_delete_draft,
    build_update_draft,
    check_notification_policy,
    confirm_and_execute,
    ensure_write_allowed,
    first_pydantic_message,
    parse_attendees,
    resolve_event,
    summary_for_create,
    summary_for_delete,
    summary_for_update,
    verify_unchanged,
)

DEFAULT_EVENT_MINUTES = 60  # used, and labelled in the preview, when the user gives no length
QUESTION = "QUESTION: "
ERROR = "ERROR: "


def _reminder_arg(value: int | None) -> int | None:
    """Tool argument -> model value: >0 minutes, 0 = no reminder, <0 = calendar default."""
    if value is None or value < 0:
        return None
    return REMINDER_OFF if value == 0 else value


def _event_window(rt: Runtime, on: str, now: datetime) -> tuple[datetime, datetime]:
    window = resolve_window(on, now=now, tz=rt.tz)
    return window.start, window.end


_REJECTIONS = {"", "no", "cancel", "stop", "nope", "nah", "expired", "discard", "不要", "取消"}


def _is_rejection(reply: str) -> bool:
    """Replies that mean 'drop this draft'. Anything else keeps it (e.g. a clarifying question)."""
    return reply.strip().lower().rstrip(".!") in _REJECTIONS


def _guard(func: Callable[[], str]) -> str:
    try:
        return func()
    except DateAmbiguity as exc:
        return QUESTION + exc.question
    except AgentError as exc:
        return ERROR + exc.message + (f" ({exc.hint})" if exc.hint else "")


def _now(rt: Runtime) -> datetime:
    return datetime.now(rt.tz)


# ---- reads ----------------------------------------------------------------------------------
def now_text(rt: Runtime) -> str:
    now = _now(rt)
    return f"Now: {now:%A %d %B %Y, %H:%M} ({rt.settings.timezone_name})"


def list_calendars_text(rt: Runtime) -> str:
    return _guard(lambda: format_calendars(rt.calendar.list_calendars_cached()))


def list_events_text(rt: Runtime, when: str = "", calendar: str = "") -> str:
    def run() -> str:
        window = resolve_window(when, now=_now(rt), tz=rt.tz)
        target = resolve_calendar(rt.calendar, calendar)
        events = rt.calendar.list_events(window.start, window.end, calendar_id=target.id)
        header = f"Events for {window.label}:"
        body = format_event_list(
            events,
            window.start,
            window.end,
            rt.tz,
            calendar_id=target.id,
            calendar_name=target.name,
        )
        return f"{header}\n{body}"

    return _guard(run)


def find_free_slots_text(
    rt: Runtime,
    day: str,
    duration_minutes: int = 30,
    earliest: str = "09:00",
    latest: str = "18:00",
    calendar: str = "",
) -> str:
    def run() -> str:
        window = resolve_window(day, now=_now(rt), tz=rt.tz)
        start_t = resolve_time(earliest) or time(9, 0)
        end_t = resolve_time(latest) or time(18, 0)
        target = resolve_calendar(rt.calendar, calendar)
        busy = [
            (e.start, e.end)
            for e in find_conflicts_everywhere(rt.calendar, window.start, window.end, target=target)
            if not e.is_all_day
        ]
        slots: list[tuple[datetime, datetime]] = []
        now = _now(rt)
        day_cursor = window.start
        while day_cursor < window.end:
            cursor = datetime.combine(day_cursor.date(), start_t, rt.tz)
            if cursor < now:  # never offer a slot that has already passed
                rounded = now.replace(second=0, microsecond=0)
                rounded += timedelta(minutes=(15 - rounded.minute % 15) % 15)
                cursor = max(cursor, rounded)
            day_end = datetime.combine(day_cursor.date(), end_t, rt.tz)
            for b_start, b_end in sorted(busy):
                if b_end <= cursor or b_start >= day_end:
                    continue
                if b_start - cursor >= timedelta(minutes=duration_minutes):
                    slots.append((cursor, b_start))
                cursor = max(cursor, b_end)
            if day_end - cursor >= timedelta(minutes=duration_minutes):
                slots.append((cursor, day_end))
            day_cursor += timedelta(days=1)
        if not slots:
            return (
                f"No free slot of {duration_minutes} minutes between {start_t:%H:%M} and "
                f"{end_t:%H:%M} for {window.label}."
            )
        lines = [f"Free slots of at least {duration_minutes} minutes for {window.label}:"]
        lines += [f"  {s:%a %d %b %H:%M}–{e:%H:%M}" for s, e in slots[:12]]
        return "\n".join(lines)

    return _guard(run)


def list_tasks_text(rt: Runtime, when: str = "", include_completed: bool = False) -> str:
    def run() -> str:
        if rt.tasks is None:
            raise AgentError("Google Tasks is not enabled in this configuration.")
        window = resolve_window(when, now=_now(rt), tz=rt.tz)
        tasks = rt.tasks.list_all_tasks(
            window.start, window.end, include_completed=include_completed
        )
        return format_tasks(tasks, window.start, window.end, rt.tz)

    return _guard(run)


# ---- drafts ---------------------------------------------------------------------------------
def _saved(rt: Runtime, draft_id: str, preview: str, conflicts: str) -> str:
    return (
        f"{preview}\n{conflicts}\n\nDraft saved as {draft_id}. Nothing has been written. "
        f"Ask the user to confirm; apply only with confirm_draft('{draft_id}', <exact reply>)."
    )


def draft_create_event_text(
    rt: Runtime,
    subject: str,
    when: str,
    duration_minutes: int | None = None,
    end: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    send_invitations: bool = False,
    calendar: str = "",
    link: str = "",
    reminder_minutes_before: int | None = DEFAULT_REMINDER_MINUTES,
) -> str:
    def run() -> str:
        ensure_write_allowed(rt)
        now = _now(rt)
        title, place = clean_subject_and_location(subject, location)
        start_dt = resolve_moment(when, now=now, tz=rt.tz)
        if start_dt < now - timedelta(minutes=1):
            raise DateAmbiguity(
                f"{start_dt:%A %d %b at %H:%M} has already passed. Which day do you mean for "
                f"'{title}'?"
            )
        # a time-only end ("3pm") lands on the start's day, not today
        end_dt = (
            resolve_moment(end, now=now, tz=rt.tz, default_day=start_dt.date()) if end else None
        )
        defaulted = False
        if end_dt is None:
            minutes = duration_minutes or DEFAULT_EVENT_MINUTES
            defaulted = not duration_minutes
            end_dt = start_dt + timedelta(minutes=minutes)
        if end_dt <= start_dt:
            raise AgentError("The end time must be after the start time.")
        people = parse_attendees(attendees)
        check_notification_policy(
            rt, has_attendees=bool(people), acknowledged=send_invitations, flag="send_invitations"
        )
        target = resolve_calendar(rt.calendar, calendar, for_write=True)
        try:
            draft = EventDraft(
                subject=title,
                start=start_dt,
                end=end_dt,
                location=place,
                duration_defaulted=defaulted,
                attendees=people,
                send_invitations=send_invitations,
                calendar_id=target.id,
                calendar_name=target.name,
                link=link or None,
                reminder_minutes_before=_reminder_arg(reminder_minutes_before),
            )
        except ValueError as exc:
            raise AgentError(first_pydantic_message(exc)) from exc
        conflicts = find_conflicts_everywhere(rt.calendar, draft.start, draft.end, target=target)
        saved = rt.drafts.save(draft)
        s = summary_for_create(draft)
        rt.audit.record(
            action="create",
            stage="proposed",
            draft_id=saved.id,
            subject=s.subject,
            start=s.start,
            end=s.end,
            extra={"calendar_id": s.calendar_id, "via": "mcp"},
        )
        return _saved(
            rt, saved.id, format_create_preview(draft, rt.tz), format_conflicts(conflicts, rt.tz)
        )

    return _guard(run)


def draft_update_event_text(
    rt: Runtime,
    find: str = "",
    on: str = "",
    event_id: str = "",
    new_when: str = "",
    new_duration_minutes: int | None = None,
    new_end: str = "",
    new_subject: str = "",
    new_location: str = "",
    notify_attendees: bool = False,
    calendar: str = "",
    new_link: str | None = None,
    new_reminder_minutes_before: int | None = None,
) -> str:
    def run() -> str:
        ensure_write_allowed(rt)
        now = _now(rt)
        target = resolve_calendar(rt.calendar, calendar, for_write=True)
        event = resolve_event(
            rt,
            event_id=event_id or None,
            find=find or None,
            on=None,
            days=14,
            calendar_id=target.id,
            window=_event_window(rt, on, now) if on else None,
        )
        # time-only phrases ("3pm") stay on the event's own day: "move it to 3pm" keeps the date
        anchor = event.start.astimezone(rt.tz).date()
        start_text = (
            resolve_moment(new_when, now=now, tz=rt.tz, default_day=anchor).isoformat()
            if new_when
            else None
        )
        end_anchor = (
            datetime.fromisoformat(start_text).astimezone(rt.tz).date() if start_text else anchor
        )
        end_text = (
            resolve_moment(new_end, now=now, tz=rt.tz, default_day=end_anchor).isoformat()
            if new_end
            else None
        )
        draft = build_update_draft(
            rt,
            event,
            subject=new_subject or None,
            start_text=start_text,
            end_text=end_text,
            duration_minutes=new_duration_minutes,
            location=new_location or None,
            notify_attendees=notify_attendees,
            calendar_name=target.name,
            link=new_link,
            reminder_minutes_before=(
                _reminder_arg(new_reminder_minutes_before)
                if new_reminder_minutes_before is not None and new_reminder_minutes_before >= 0
                else None
            ),
            use_default_reminder=(
                new_reminder_minutes_before is not None and new_reminder_minutes_before < 0
            ),
        )
        conflicts = find_conflicts_everywhere(
            rt.calendar,
            draft.effective_start,
            draft.effective_end,
            exclude_id=event.id,
            target=target,
        )
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
            extra={"calendar_id": s.calendar_id, "via": "mcp"},
        )
        return _saved(
            rt, saved.id, format_update_preview(draft, rt.tz), format_conflicts(conflicts, rt.tz)
        )

    return _guard(run)


def draft_delete_event_text(
    rt: Runtime,
    find: str = "",
    on: str = "",
    event_id: str = "",
    notify_attendees: bool = False,
    calendar: str = "",
) -> str:
    def run() -> str:
        ensure_write_allowed(rt)
        now = _now(rt)
        target = resolve_calendar(rt.calendar, calendar, for_write=True)
        event = resolve_event(
            rt,
            event_id=event_id or None,
            find=find or None,
            on=None,
            days=14,
            calendar_id=target.id,
            window=_event_window(rt, on, now) if on else None,
        )
        draft = build_delete_draft(
            rt, event, notify_attendees=notify_attendees, calendar_name=target.name
        )
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
            extra={"calendar_id": s.calendar_id, "via": "mcp"},
        )
        return _saved(
            rt,
            saved.id,
            format_delete_preview(draft, rt.tz),
            "This deletion is permanent.",
        )

    return _guard(run)


def list_drafts_text(rt: Runtime) -> str:
    drafts = rt.drafts.list()
    if not drafts:
        return "No pending drafts."
    return "Pending drafts:\n" + "\n".join(
        f"  {format_draft_line(d.id, d.payload, rt.tz)}" for d in drafts
    )


def discard_draft_text(rt: Runtime, draft_id: str) -> str:
    def run() -> str:
        draft = rt.drafts.load(draft_id)
        rt.drafts.discard(draft_id)
        rt.audit.record(action=draft.kind, stage="rejected", draft_id=draft_id, detail="discarded")  # type: ignore[arg-type]
        return f"Draft {draft_id} discarded. Nothing was changed."

    return _guard(run)


def confirm_draft_text(rt: Runtime, draft_id: str, user_reply: str) -> str:
    """Apply a draft only if ``user_reply`` is the single word ``yes`` (any case); else discard."""

    def run() -> str:
        ensure_write_allowed(rt)
        draft = rt.drafts.load(draft_id)
        payload = draft.payload
        if not is_exact_confirmation(user_reply):
            if _is_rejection(user_reply):
                rt.drafts.discard(draft_id)
                rt.audit.record(
                    action=draft.kind,  # type: ignore[arg-type]
                    stage="rejected",
                    draft_id=draft_id,
                    detail=f"reply was not 'yes': {user_reply[:40]!r}",
                )
                return f"Not confirmed. Draft {draft_id} discarded; nothing was changed."
            return (
                f"Not confirmed. Draft {draft_id} is kept; reply yes to apply it or cancel to "
                "discard it."
            )

        silent: list[str] = []
        reader = lambda _prompt: "yes"  # noqa: E731 - the literal 'yes' was verified above
        try:
            if isinstance(payload, EventDraft):
                created = confirm_and_execute(
                    rt,
                    summary=summary_for_create(payload),
                    draft_id=draft_id,
                    question="Create this event?",
                    perform=lambda: rt.calendar.create_event(payload),
                    echo=silent.append,
                    reader=reader,
                )
                rt.drafts.discard(draft_id)
                return (
                    f"Created: {created.subject} {format_dt(created.start, rt.tz)} "
                    f"(event id {created.id})."
                )
            if isinstance(payload, UpdateDraft):
                verify_unchanged(rt, payload.original)
                updated = confirm_and_execute(
                    rt,
                    summary=summary_for_update(payload),
                    draft_id=draft_id,
                    question="Update this event?",
                    perform=lambda: rt.calendar.update_event(payload),
                    echo=silent.append,
                    reader=reader,
                )
                rt.drafts.discard(draft_id)
                return f"Updated: {updated.subject} now {format_dt(updated.start, rt.tz)}."
            if isinstance(payload, DeleteDraft):
                verify_unchanged(rt, payload.original)
                confirm_and_execute(
                    rt,
                    summary=summary_for_delete(payload),
                    draft_id=draft_id,
                    question="Delete this event permanently?",
                    perform=lambda: rt.calendar.delete_event(payload),
                    echo=silent.append,
                    reader=reader,
                )
                rt.drafts.discard(draft_id)
                o = payload.original
                return f"Deleted: {o.subject} {format_dt(o.start, rt.tz)}."
        except UserCancelledError:  # cannot happen with a verified 'yes', kept for safety
            return f"Not confirmed. Draft {draft_id} kept."
        raise AgentError(f"Unknown draft kind for {draft_id}.")

    return _guard(run)
