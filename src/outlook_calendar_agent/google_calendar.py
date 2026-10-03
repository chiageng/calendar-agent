"""Google Calendar backend. All reads are time-bounded ``events.list`` queries on one calendar."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any
from urllib.parse import quote

from .backend import PRIMARY, CalendarBackend
from .google_auth import GoogleAuthenticator
from .google_client import GoogleClient
from .models import CalendarEvent, CalendarInfo, DeleteDraft, EventDraft, UpdateDraft
from .timeutil import to_rfc3339_utc, zone_name

PAGE_SIZE = 250
# Keep responses small: only the fields CalendarEvent.from_google reads.
EVENT_FIELDS = (
    "id,summary,start,end,location,organizer,status,attendees(email,displayName,optional,"
    "organizer),recurringEventId,recurrence,hangoutLink,htmlLink,etag"
)
LIST_FIELDS = f"nextPageToken,items({EVENT_FIELDS})"
CALENDAR_LIST_FIELDS = "nextPageToken,items(id,summary,summaryOverride,primary,accessRole)"
WRITABLE_ROLES = {"writer", "owner"}


class GoogleCalendarService(CalendarBackend):
    name = "Google Calendar"
    can_suppress_notifications = True  # sendUpdates=none

    def __init__(self, client: GoogleClient, auth: GoogleAuthenticator, tz: tzinfo) -> None:
        super().__init__(tz)
        self._client = client
        self._auth = auth

    @staticmethod
    def _calendar_path(calendar_id: str) -> str:
        return f"/calendars/{quote(calendar_id or PRIMARY, safe='')}"

    def _event_path(self, calendar_id: str, event_id: str) -> str:
        return f"{self._calendar_path(calendar_id)}/events/{quote(event_id, safe='')}"

    @staticmethod
    def _send_updates(notify: bool) -> dict[str, str]:
        return {"sendUpdates": "all" if notify else "none"}

    # -- reads -----------------------------------------------------------------------
    def get_me(self) -> dict[str, Any]:
        info = self._auth.userinfo()
        return {
            "displayName": info.get("name") or info.get("email") or "(unknown)",
            "mail": info.get("email"),
        }

    def list_calendars(self) -> list[CalendarInfo]:
        raw = self._client.get_all(
            "/users/me/calendarList", params={"fields": CALENDAR_LIST_FIELDS, "maxResults": "250"}
        )
        calendars = [
            CalendarInfo(
                id=item["id"],
                name=item.get("summaryOverride") or item.get("summary") or item["id"],
                is_primary=bool(item.get("primary")),
                can_write=item.get("accessRole") in WRITABLE_ROLES,
            )
            for item in raw
        ]
        return sorted(calendars, key=lambda c: (not c.is_primary, c.name.lower()))

    def list_events(
        self, start: datetime, end: datetime, *, calendar_id: str = PRIMARY
    ) -> list[CalendarEvent]:
        if end <= start:
            raise ValueError("end must be after start")
        params = {
            "timeMin": to_rfc3339_utc(start),
            "timeMax": to_rfc3339_utc(end),
            "singleEvents": "true",  # expand recurring series into instances
            "orderBy": "startTime",
            "maxResults": str(PAGE_SIZE),
            "timeZone": zone_name(self._tz),
            "fields": LIST_FIELDS,
        }
        raw = self._client.get_all(f"{self._calendar_path(calendar_id)}/events", params=params)
        events = [
            CalendarEvent.from_google(item, self._tz, calendar_id=calendar_id) for item in raw
        ]
        events.sort(key=lambda e: e.start)
        return events

    def get_event(self, event_id: str, *, calendar_id: str = PRIMARY) -> CalendarEvent:
        raw = self._client.get(
            self._event_path(calendar_id, event_id), params={"fields": EVENT_FIELDS}
        )
        return CalendarEvent.from_google(raw, self._tz, calendar_id=calendar_id)

    # -- writes (callers must have obtained explicit confirmation first) ---------------
    def create_event(self, draft: EventDraft) -> CalendarEvent:
        raw = self._client.post(
            f"{self._calendar_path(draft.calendar_id)}/events",
            json=draft.to_google_payload(self._tz),
            params={**self._send_updates(draft.send_invitations), "fields": EVENT_FIELDS},
        )
        return CalendarEvent.from_google(raw, self._tz, calendar_id=draft.calendar_id)

    def update_event(self, draft: UpdateDraft) -> CalendarEvent:
        original = draft.original
        headers = {"If-Match": original.change_key} if original.change_key else None
        raw = self._client.patch(
            self._event_path(original.calendar_id, original.id),
            json=draft.to_google_patch(self._tz),
            params={**self._send_updates(draft.notify_attendees), "fields": EVENT_FIELDS},
            headers=headers,
        )
        return CalendarEvent.from_google(raw, self._tz, calendar_id=original.calendar_id)

    def delete_event(self, draft: DeleteDraft) -> None:
        original = draft.original
        headers = {"If-Match": original.change_key} if original.change_key else None
        self._client.delete(
            self._event_path(original.calendar_id, original.id),
            params=self._send_updates(draft.notify_attendees),
            headers=headers,
        )
