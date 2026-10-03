"""Append-only JSONL audit log of proposed/confirmed/rejected/succeeded/failed mutations.

Never receives tokens or credentials; keys that look like secrets are rejected outright.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .storage import append_private_line

Stage = Literal["proposed", "confirmed", "rejected", "succeeded", "failed"]
Action = Literal["create", "update", "delete"]

_FORBIDDEN_KEY_PARTS = ("token", "secret", "authorization", "password", "credential", "bearer")


class AuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        *,
        action: Action,
        stage: Stage,
        draft_id: str | None = None,
        event_id: str | None = None,
        subject: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "action": action,
            "stage": stage,
            "draft_id": draft_id,
            "event_id": event_id,
            "subject": subject,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "detail": detail,
        }
        if extra:
            entry.update(extra)
        entry = {k: v for k, v in entry.items() if v is not None}
        for key in entry:
            lowered = key.lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError(f"Refusing to audit-log sensitive key {key!r}")
        append_private_line(self._path, json.dumps(entry, ensure_ascii=False))
        return entry
