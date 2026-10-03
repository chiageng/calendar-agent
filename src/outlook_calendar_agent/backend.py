"""Provider-neutral calendar backend interface shared by the Google and Microsoft services."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, tzinfo
from typing import Any

from .models import CalendarEvent, CalendarInfo, DeleteDraft, EventDraft, UpdateDraft

PRIMARY = "primary"


class CalendarBackend(ABC):
    """What the CLI needs from a calendar provider. Writes assume confirmation already happened.

    ``calendar_id`` is ``"primary"`` for the account's main calendar or a provider calendar ID.
    """

    #: Human-readable provider name used in messages.
    name: str = "calendar"
    #: True when attendee notifications can be suppressed (Google ``sendUpdates=none``).
    can_suppress_notifications: bool = False

    def __init__(self, tz: tzinfo) -> None:
        self._tz = tz

    @property
    def tz(self) -> tzinfo:
        return self._tz

    # -- reads -----------------------------------------------------------------------
    @abstractmethod
    def get_me(self) -> dict[str, Any]:
        """Return at least ``displayName`` and the account address for the signed-in user."""

    @abstractmethod
    def list_calendars(self) -> list[CalendarInfo]:
        """All calendars the account can see."""

    @abstractmethod
    def list_events(
        self, start: datetime, end: datetime, *, calendar_id: str = PRIMARY
    ) -> list[CalendarEvent]:
        """Time-bounded query of one calendar, sorted by start."""

    @abstractmethod
    def get_event(self, event_id: str, *, calendar_id: str = PRIMARY) -> CalendarEvent: ...

    def search_events(
        self, start: datetime, end: datetime, text: str, *, calendar_id: str = PRIMARY
    ) -> list[CalendarEvent]:
        needle = text.strip().lower()
        return [
            e
            for e in self.list_events(start, end, calendar_id=calendar_id)
            if needle in e.subject.lower() and not e.is_cancelled
        ]

    def find_conflicts(
        self,
        start: datetime,
        end: datetime,
        *,
        exclude_id: str | None = None,
        calendar_id: str = PRIMARY,
    ) -> list[CalendarEvent]:
        return [
            e
            for e in self.list_events(start, end, calendar_id=calendar_id)
            if e.overlaps(start, end) and not e.is_cancelled and e.id != exclude_id
        ]

    # -- writes ----------------------------------------------------------------------
    @abstractmethod
    def create_event(self, draft: EventDraft) -> CalendarEvent: ...

    @abstractmethod
    def update_event(self, draft: UpdateDraft) -> CalendarEvent: ...

    @abstractmethod
    def delete_event(self, draft: DeleteDraft) -> None: ...
