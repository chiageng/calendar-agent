"""Microsoft Graph backend. All reads are time-bounded ``calendarView`` queries."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any
from urllib.parse import quote

from .backend import PRIMARY, CalendarBackend
from .errors import UnsupportedOperationError
from .graph_client import GraphClient
from .models import (
    DETAIL_SELECT,
    LIST_SELECT,
    CalendarEvent,
    CalendarInfo,
    DeleteDraft,
    EventDraft,
    UpdateDraft,
)
from .timeutil import to_query_utc, zone_name

ME_SELECT = "displayName,mail,userPrincipalName"
CALENDARS_SELECT = "id,name,isDefaultCalendar,canEdit"
DEFAULT_PAGE_SIZE = 50


class GraphCalendarService(CalendarBackend):
    name = "Microsoft 365 / Outlook"
    can_suppress_notifications = False  # Graph always notifies attendees of organizer changes

    def __init__(self, client: GraphClient, tz: tzinfo) -> None:
        super().__init__(tz)
        self._client = client

    def _prefer_headers(self) -> dict[str, str]:
        return {"Prefer": f'outlook.timezone="{zone_name(self._tz)}"'}

    @staticmethod
    def _base(calendar_id: str) -> str:
        if not calendar_id or calendar_id == PRIMARY:
            return "/me"
        return f"/me/calendars/{quote(calendar_id, safe='')}"

    def _event_path(self, calendar_id: str, event_id: str) -> str:
        return f"{self._base(calendar_id)}/events/{quote(event_id, safe='')}"

    @staticmethod
    def _require_notification_consent(has_attendees: bool, acknowledged: bool, flag: str) -> None:
        if has_attendees and not acknowledged:
            raise UnsupportedOperationError(
                f"Microsoft Graph always notifies attendees of this change; pass {flag} to "
                "acknowledge it."
            )

    # -- reads -----------------------------------------------------------------------
    def get_me(self) -> dict[str, Any]:
        me = self._client.get("/me", params={"$select": ME_SELECT})
        return {
            "displayName": me.get("displayName"),
            "mail": me.get("mail") or me.get("userPrincipalName"),
        }

    def list_calendars(self) -> list[CalendarInfo]:
        raw = self._client.get_all("/me/calendars", params={"$select": CALENDARS_SELECT})
        calendars = [
            CalendarInfo(
                id=item["id"],
                name=item.get("name") or item["id"],
                is_primary=bool(item.get("isDefaultCalendar")),
                can_write=bool(item.get("canEdit")),
            )
            for item in raw
        ]
        return sorted(calendars, key=lambda c: (not c.is_primary, c.name.lower()))

    def list_events(
        self,
        start: datetime,
        end: datetime,
        *,
        calendar_id: str = PRIMARY,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> list[CalendarEvent]:
        if end <= start:
            raise ValueError("end must be after start")
        params = {
            "startDateTime": to_query_utc(start),
            "endDateTime": to_query_utc(end),
            "$select": ",".join(LIST_SELECT),
            "$orderby": "start/dateTime",
            "$top": str(page_size),
        }
        raw = self._client.get_all(
            f"{self._base(calendar_id)}/calendarView", params=params, headers=self._prefer_headers()
        )
        events = [CalendarEvent.from_graph(item, calendar_id=calendar_id) for item in raw]
        events.sort(key=lambda e: e.start)
        return events

    def get_event(self, event_id: str, *, calendar_id: str = PRIMARY) -> CalendarEvent:
        raw = self._client.get(
            self._event_path(calendar_id, event_id),
            params={"$select": ",".join(DETAIL_SELECT)},
            headers=self._prefer_headers(),
        )
        return CalendarEvent.from_graph(raw, calendar_id=calendar_id)

    # -- writes (callers must have obtained explicit confirmation first) ---------------
    def create_event(self, draft: EventDraft) -> CalendarEvent:
        self._require_notification_consent(
            bool(draft.attendees), draft.send_invitations, "--send-invitations"
        )
        raw = self._client.post(
            f"{self._base(draft.calendar_id)}/events",
            json=draft.to_graph_payload(self._tz),
            headers=self._prefer_headers(),
        )
        return CalendarEvent.from_graph(raw, calendar_id=draft.calendar_id)

    def update_event(self, draft: UpdateDraft) -> CalendarEvent:
        self._require_notification_consent(
            bool(draft.original.attendees), draft.notify_attendees, "--notify-attendees"
        )
        raw = self._client.patch(
            self._event_path(draft.original.calendar_id, draft.original.id),
            json=draft.to_graph_patch(self._tz),
            headers=self._prefer_headers(),
        )
        return CalendarEvent.from_graph(raw, calendar_id=draft.original.calendar_id)

    def delete_event(self, draft: DeleteDraft) -> None:
        self._require_notification_consent(
            bool(draft.original.attendees), draft.notify_attendees, "--notify-attendees"
        )
        self._client.delete(self._event_path(draft.original.calendar_id, draft.original.id))
