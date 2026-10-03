"""Google Calendar backend. All reads are time-bounded ``events.list`` queries on ``primary``."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any
from urllib.parse import quote

from .backend import CalendarBackend
from .google_auth import GoogleAuthenticator
from .google_client import GoogleClient
from .models import CalendarEvent, DeleteDraft, EventDraft, UpdateDraft
from .timeutil import to_rfc3339_utc, zone_name

CALENDAR_ID = "primary"
PAGE_SIZE = 250
# Keep responses small: only the fields CalendarEvent.from_google reads.
EVENT_FIELDS = (
    "id,summary,start,end,location,organizer,status,attendees(email,displayName,optional,"
    "organizer),recurringEventId,recurrence,hangoutLink,htmlLink,etag"
)
LIST_FIELDS = f"nextPageToken,items({EVENT_FIELDS})"


class GoogleCalendarService(CalendarBackend):
    name = "Google Calendar"
    can_suppress_notifications = True  # sendUpdates=none

    def __init__(self, client: GoogleClient, auth: GoogleAuthenticator, tz: tzinfo) -> None:
        super().__init__(tz)
        self._client = client
        self._auth = auth

    @staticmethod
    def _event_path(event_id: str) -> str:
        return f"/calendars/{CALENDAR_ID}/events/{quote(event_id, safe='')}"

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

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
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
        raw = self._client.get_all(f"/calendars/{CALENDAR_ID}/events", params=params)
        events = [CalendarEvent.from_google(item, self._tz) for item in raw]
        events.sort(key=lambda e: e.start)
        return events

    def get_event(self, event_id: str) -> CalendarEvent:
        raw = self._client.get(self._event_path(event_id), params={"fields": EVENT_FIELDS})
        return CalendarEvent.from_google(raw, self._tz)

    # -- writes (callers must have obtained explicit confirmation first) ---------------
    def create_event(self, draft: EventDraft) -> CalendarEvent:
        raw = self._client.post(
            f"/calendars/{CALENDAR_ID}/events",
            json=draft.to_google_payload(self._tz),
            params={**self._send_updates(draft.send_invitations), "fields": EVENT_FIELDS},
        )
        return CalendarEvent.from_google(raw, self._tz)

    def update_event(self, draft: UpdateDraft) -> CalendarEvent:
        headers = {"If-Match": draft.original.change_key} if draft.original.change_key else None
        raw = self._client.patch(
            self._event_path(draft.original.id),
            json=draft.to_google_patch(self._tz),
            params={**self._send_updates(draft.notify_attendees), "fields": EVENT_FIELDS},
            headers=headers,
        )
        return CalendarEvent.from_google(raw, self._tz)

    def delete_event(self, draft: DeleteDraft) -> None:
        headers = {"If-Match": draft.original.change_key} if draft.original.change_key else None
        self._client.delete(
            self._event_path(draft.original.id),
            params=self._send_updates(draft.notify_attendees),
            headers=headers,
        )
