"""Calendar selection helpers shared by read and write commands."""

from __future__ import annotations

from datetime import datetime

from .backend import PRIMARY, CalendarBackend
from .errors import AgentError
from .models import CalendarEvent, CalendarInfo


def primary_calendar(backend: CalendarBackend) -> CalendarInfo:
    """The account's primary calendar, with its real ID and name when the provider reports one."""
    for cal in backend.list_calendars():
        if cal.is_primary:
            return cal
    return CalendarInfo(id=PRIMARY, name="primary", is_primary=True, can_write=True)


def resolve_calendar(
    backend: CalendarBackend, text: str | None, *, for_write: bool = False
) -> CalendarInfo:
    """Map ``--calendar`` input (exact ID or a unique name) to a calendar.

    ``None``/blank/``primary`` means the primary calendar and never touches the network. With
    ``for_write`` the calendar must be writable, so a bad choice fails before any draft exists.
    """
    wanted = (text or "").strip()
    if not wanted or wanted.lower() == PRIMARY:
        return CalendarInfo(id=PRIMARY, name=PRIMARY, is_primary=True, can_write=True)

    calendars = backend.list_calendars()
    matches = [c for c in calendars if c.id.casefold() == wanted.casefold()]
    if not matches:
        matches = [c for c in calendars if c.name.casefold() == wanted.casefold()]
    if not matches:
        matches = [c for c in calendars if wanted.casefold() in c.name.casefold()]

    if not matches:
        listing = "\n".join(f"  {c.display()}" for c in calendars) or "  (none visible)"
        raise AgentError(
            f"No calendar named {wanted!r}.", hint=f"Calendars you can see:\n{listing}"
        )
    if len(matches) > 1:
        raise AgentError(
            f"Several calendars match {wanted!r}; pass the exact id:\n"
            + "\n".join(f"  {c.display()}" for c in matches)
        )
    chosen = matches[0]
    if for_write and not chosen.can_write:
        raise AgentError(
            f"Calendar {chosen.name!r} is read-only for this account; it cannot be changed.",
            hint="Pick a calendar marked [writable] in 'calendars'.",
        )
    return chosen


def find_conflicts_everywhere(
    backend: CalendarBackend,
    start: datetime,
    end: datetime,
    *,
    exclude_id: str | None = None,
    target: CalendarInfo,
) -> list[CalendarEvent]:
    """Conflicts across the target calendar plus every writable calendar (your own schedule).

    Read-only subscriptions such as holiday feeds are skipped to avoid all-day noise. The
    ``primary`` alias and the primary calendar's real ID are treated as the same calendar.
    """
    writable = [c for c in backend.list_calendars() if c.can_write]
    scan: list[CalendarInfo] = list(writable)
    target_is_primary = target.id == PRIMARY or target.is_primary
    if target_is_primary:
        if not any(c.is_primary for c in scan):
            scan.append(target)
    elif all(c.id.casefold() != target.id.casefold() for c in scan):
        scan.append(target)

    conflicts: list[CalendarEvent] = []
    for cal in scan:
        conflicts.extend(
            backend.find_conflicts(start, end, exclude_id=exclude_id, calendar_id=cal.id)
        )
    conflicts.sort(key=lambda e: e.start)
    return conflicts
