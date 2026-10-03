"""Pydantic models: parsed calendar events and the draft (proposal) models for writes.

Draft models build the exact Graph payloads. They never set ``isOnlineMeeting``,
``recurrence`` or ``organizer``; those are intentionally deferred.
"""

from __future__ import annotations

import re
from datetime import date, datetime, tzinfo
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .timeutil import (
    SGT,
    parse_google_datetime,
    parse_graph_datetime,
    to_google_datetime,
    to_graph_datetime,
)

_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
_NAMED_EMAIL_RE = re.compile(r"^\s*(?P<name>[^<>]*?)\s*<(?P<email>[^<>]+)>\s*$")

# Small projections for Graph $select. Listing uses the minimal set; details add write context.
LIST_SELECT: tuple[str, ...] = (
    "id",
    "subject",
    "start",
    "end",
    "isAllDay",
    "location",
    "organizer",
    "isCancelled",
    "type",
)
DETAIL_SELECT: tuple[str, ...] = LIST_SELECT + (
    "isOrganizer",
    "attendees",
    "onlineMeeting",
    "isOnlineMeeting",
    "webLink",
    "changeKey",
)

EventType = Literal["singleInstance", "occurrence", "exception", "seriesMaster"]


class Attendee(BaseModel):
    email: str
    name: str | None = None
    type: Literal["required", "optional"] = "required"

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError(f"{value!r} is not a valid e-mail address")
        return value

    @classmethod
    def parse(cls, text: str, *, type: Literal["required", "optional"] = "required") -> Attendee:
        """Accept ``alice@example.com`` or ``Alice Tan <alice@example.com>``."""
        match = _NAMED_EMAIL_RE.match(text)
        if match:
            name = match.group("name").strip().strip('"') or None
            return cls(email=match.group("email"), name=name, type=type)
        return cls(email=text, type=type)

    def display(self) -> str:
        label = f"{self.name} <{self.email}>" if self.name else self.email
        return f"{label} ({self.type})"

    def to_graph(self) -> dict[str, Any]:
        address: dict[str, str] = {"address": self.email}
        if self.name:
            address["name"] = self.name
        return {"emailAddress": address, "type": self.type}

    def to_google(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"email": self.email}
        if self.name:
            entry["displayName"] = self.name
        if self.type == "optional":
            entry["optional"] = True
        return entry


class CalendarInfo(BaseModel):
    """One calendar visible to the signed-in account."""

    id: str
    name: str
    is_primary: bool = False
    can_write: bool = False

    def display(self) -> str:
        flags = [f for f, on in (("primary", self.is_primary), ("writable", self.can_write)) if on]
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        return f"{self.name}{suffix}\n    id: {self.id}"


class TaskList(BaseModel):
    id: str
    name: str


class TaskItem(BaseModel):
    """A Google Task (read-only). Tasks have a due *date*, not a time."""

    id: str
    title: str
    due: date | None = None
    completed: bool = False
    notes: str | None = None
    list_name: str = "My Tasks"
    in_default_list: bool = True


class CalendarEvent(BaseModel):
    id: str
    calendar_id: str = "primary"
    subject: str = "(no subject)"
    start: datetime
    end: datetime
    is_all_day: bool = False
    location: str | None = None
    organizer_name: str | None = None
    organizer_email: str | None = None
    is_cancelled: bool = False
    is_organizer: bool | None = None
    event_type: str | None = None
    attendees: list[Attendee] = Field(default_factory=list)
    online_join_url: str | None = None
    web_link: str | None = None
    change_key: str | None = None

    @classmethod
    def from_graph(cls, data: dict[str, Any], *, calendar_id: str = "primary") -> CalendarEvent:
        organizer = (data.get("organizer") or {}).get("emailAddress") or {}
        location = (data.get("location") or {}).get("displayName") or None
        online = data.get("onlineMeeting") or {}
        attendees: list[Attendee] = []
        for raw in data.get("attendees") or []:
            address = (raw.get("emailAddress") or {}).get("address")
            if not address or not _EMAIL_RE.match(address):
                continue
            kind = raw.get("type") if raw.get("type") in {"required", "optional"} else "required"
            attendees.append(
                Attendee(email=address, name=(raw.get("emailAddress") or {}).get("name"), type=kind)
            )
        return cls(
            id=data["id"],
            calendar_id=calendar_id,
            subject=data.get("subject") or "(no subject)",
            start=parse_graph_datetime(data["start"]),
            end=parse_graph_datetime(data["end"]),
            is_all_day=bool(data.get("isAllDay", False)),
            location=location,
            organizer_name=organizer.get("name"),
            organizer_email=organizer.get("address"),
            is_cancelled=bool(data.get("isCancelled", False)),
            is_organizer=data.get("isOrganizer"),
            event_type=data.get("type"),
            attendees=attendees,
            online_join_url=online.get("joinUrl"),
            web_link=data.get("webLink"),
            change_key=data.get("changeKey"),
        )

    @classmethod
    def from_google(
        cls, data: dict[str, Any], tz: Any = SGT, *, calendar_id: str = "primary"
    ) -> CalendarEvent:
        organizer = data.get("organizer") or {}
        attendees: list[Attendee] = []
        for raw in data.get("attendees") or []:
            address = raw.get("email")
            if not address or raw.get("organizer") or not _EMAIL_RE.match(address):
                continue
            attendees.append(
                Attendee(
                    email=address,
                    name=raw.get("displayName"),
                    type="optional" if raw.get("optional") else "required",
                )
            )
        if data.get("recurringEventId"):
            event_type = "occurrence"
        elif data.get("recurrence"):
            event_type = "seriesMaster"
        else:
            event_type = "singleInstance"
        start_raw, end_raw = data.get("start") or {}, data.get("end") or {}
        is_all_day = "date" in start_raw and "dateTime" not in start_raw
        return cls(
            id=data["id"],
            calendar_id=calendar_id,
            subject=data.get("summary") or "(no subject)",
            start=parse_google_datetime(start_raw, tz),
            end=parse_google_datetime(end_raw, tz),
            is_all_day=is_all_day,
            location=data.get("location") or None,
            organizer_name=organizer.get("displayName"),
            organizer_email=organizer.get("email"),
            is_cancelled=data.get("status") == "cancelled",
            is_organizer=organizer.get("self") if organizer else None,
            event_type=event_type,
            attendees=attendees,
            online_join_url=data.get("hangoutLink"),
            web_link=data.get("htmlLink"),
            change_key=data.get("etag"),
        )

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and self.end > start


class EventSnapshot(BaseModel):
    """What an existing event looked like when a draft was prepared."""

    id: str
    calendar_id: str = "primary"
    calendar_name: str | None = None
    subject: str
    start: datetime
    end: datetime
    location: str | None = None
    attendees: list[Attendee] = Field(default_factory=list)
    is_organizer: bool | None = None
    event_type: str | None = None
    online_join_url: str | None = None
    change_key: str | None = None

    @classmethod
    def from_event(cls, event: CalendarEvent, *, calendar_name: str | None = None) -> EventSnapshot:
        data = event.model_dump(include=set(cls.model_fields) - {"calendar_name"})
        return cls(**data, calendar_name=calendar_name)

    @property
    def calendar_label(self) -> str:
        return _calendar_label(self.calendar_id, self.calendar_name)

    @property
    def calendar_info(self) -> CalendarInfo:
        return CalendarInfo(
            id=self.calendar_id,
            name=self.calendar_name or self.calendar_id,
            is_primary=self.calendar_id == "primary",
            can_write=True,
        )

    def matches(self, event: CalendarEvent) -> bool:
        if self.change_key and event.change_key:
            return self.change_key == event.change_key
        return self.subject == event.subject and self.start == event.start and self.end == event.end


def _calendar_label(calendar_id: str, calendar_name: str | None) -> str:
    if calendar_name and calendar_name != calendar_id:
        return f"{calendar_name} ({calendar_id})"
    return calendar_id


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{name} must include a time zone")


class EventDraft(BaseModel):
    """Proposal for creating an event (Google ``events.insert`` / Graph ``POST /me/events``)."""

    kind: Literal["create"] = "create"
    subject: str = Field(min_length=1)
    start: datetime
    end: datetime
    location: str | None = None
    body: str | None = None
    attendees: list[Attendee] = Field(default_factory=list)
    send_invitations: bool = False
    calendar_id: str = "primary"
    calendar_name: str | None = None

    @property
    def calendar_label(self) -> str:
        return _calendar_label(self.calendar_id, self.calendar_name)

    @property
    def calendar_info(self) -> CalendarInfo:
        return CalendarInfo(
            id=self.calendar_id,
            name=self.calendar_name or self.calendar_id,
            is_primary=self.calendar_id == "primary",
            can_write=True,
        )

    @model_validator(mode="after")
    def _validate(self) -> EventDraft:
        _require_aware("start", self.start)
        _require_aware("end", self.end)
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self

    @property
    def invitations_will_be_sent(self) -> bool:
        return bool(self.attendees) and self.send_invitations

    def to_graph_payload(self, tz: tzinfo = SGT) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject": self.subject,
            "start": to_graph_datetime(self.start, tz),
            "end": to_graph_datetime(self.end, tz),
        }
        if self.body:
            payload["body"] = {"contentType": "text", "content": self.body}
        if self.location:
            payload["location"] = {"displayName": self.location}
        if self.attendees:
            payload["attendees"] = [a.to_graph() for a in self.attendees]
        return payload

    def to_google_payload(self, tz: tzinfo = SGT) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary": self.subject,
            "start": to_google_datetime(self.start, tz),
            "end": to_google_datetime(self.end, tz),
        }
        if self.body:
            payload["description"] = self.body
        if self.location:
            payload["location"] = self.location
        if self.attendees:
            payload["attendees"] = [a.to_google() for a in self.attendees]
        return payload


class UpdateDraft(BaseModel):
    """Proposal for a partial update (Google ``events.patch`` / Graph ``PATCH``)."""

    kind: Literal["update"] = "update"
    original: EventSnapshot
    subject: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    notify_attendees: bool = False

    @model_validator(mode="after")
    def _validate(self) -> UpdateDraft:
        _require_aware("start", self.start)
        _require_aware("end", self.end)
        if not self.changed_fields():
            raise ValueError("no changes requested")
        if self.effective_end <= self.effective_start:
            raise ValueError("end must be after start")
        return self

    @property
    def effective_start(self) -> datetime:
        return self.start or self.original.start

    @property
    def effective_end(self) -> datetime:
        return self.end or self.original.end

    @property
    def notices_will_be_sent(self) -> bool:
        return bool(self.original.attendees) and self.notify_attendees

    def changed_fields(self) -> list[str]:
        changed = []
        if self.subject is not None and self.subject != self.original.subject:
            changed.append("subject")
        if self.start is not None and self.start != self.original.start:
            changed.append("start")
        if self.end is not None and self.end != self.original.end:
            changed.append("end")
        if self.location is not None and self.location != (self.original.location or ""):
            changed.append("location")
        return changed

    def to_graph_patch(self, tz: tzinfo = SGT) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        changed = self.changed_fields()
        if "subject" in changed:
            patch["subject"] = self.subject
        if "start" in changed or "end" in changed:
            # Always send both so the server never has to infer one from the other.
            patch["start"] = to_graph_datetime(self.effective_start, tz)
            patch["end"] = to_graph_datetime(self.effective_end, tz)
        if "location" in changed:
            patch["location"] = {"displayName": self.location}
        return patch

    def to_google_patch(self, tz: tzinfo = SGT) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        changed = self.changed_fields()
        if "subject" in changed:
            patch["summary"] = self.subject
        if "start" in changed or "end" in changed:
            patch["start"] = to_google_datetime(self.effective_start, tz)
            patch["end"] = to_google_datetime(self.effective_end, tz)
        if "location" in changed:
            patch["location"] = self.location
        return patch


class DeleteDraft(BaseModel):
    """Proposal for deleting an event (Google ``events.delete`` / Graph ``DELETE``)."""

    kind: Literal["delete"] = "delete"
    original: EventSnapshot
    notify_attendees: bool = False

    @property
    def cancellations_will_be_sent(self) -> bool:
        return bool(self.original.attendees) and self.notify_attendees


DraftPayload = EventDraft | UpdateDraft | DeleteDraft


class Draft(BaseModel):
    id: str
    created_at: datetime
    payload: DraftPayload = Field(discriminator="kind")

    @property
    def kind(self) -> str:
        return self.payload.kind
