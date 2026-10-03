"""Lazy composition of settings, authentication, API client and services for the CLI.

Nothing here touches the network until a command actually calls the provider.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .audit import AuditLog
from .backend import CalendarBackend
from .config import Settings, load_settings
from .drafts import DraftStore
from .google_tasks import GoogleTasksService


class Authenticator(Protocol):
    """Common surface of GoogleAuthenticator and the Microsoft Authenticator."""

    def login(self, echo: Callable[[str], None], **kwargs: Any) -> dict[str, Any]: ...

    def acquire_token(self) -> str: ...

    def logout(self) -> int: ...

    def signed_in_email(self) -> str | None: ...


@dataclass
class Runtime:
    settings: Settings
    auth: Authenticator
    calendar: CalendarBackend
    audit: AuditLog
    drafts: DraftStore
    tasks: GoogleTasksService | None = None

    @property
    def tz(self) -> ZoneInfo:
        return self.settings.timezone


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or load_settings()
    auth: Authenticator
    calendar: CalendarBackend
    tasks: GoogleTasksService | None = None
    if settings.provider == "google":
        from .google_auth import GoogleAuthenticator
        from .google_calendar import GoogleCalendarService
        from .google_client import GoogleClient
        from .google_tasks import GOOGLE_TASKS_BASE_URL

        assert settings.google is not None
        google_auth = GoogleAuthenticator(settings.google, settings.token_cache_path)
        client = GoogleClient(google_auth.acquire_token, base_url=settings.google.base_url)
        auth = google_auth
        calendar = GoogleCalendarService(client, google_auth, settings.timezone)
        if settings.google.wants_tasks:
            tasks_client = GoogleClient(google_auth.acquire_token, base_url=GOOGLE_TASKS_BASE_URL)
            tasks = GoogleTasksService(tasks_client, settings.timezone)
    else:
        from .graph_auth import Authenticator as GraphAuthenticator
        from .graph_calendar import GraphCalendarService
        from .graph_client import GraphClient

        assert settings.microsoft is not None
        graph_auth = GraphAuthenticator(settings.microsoft, settings.token_cache_path)
        graph_client = GraphClient(graph_auth.acquire_token, base_url=settings.microsoft.base_url)
        auth = graph_auth
        calendar = GraphCalendarService(graph_client, settings.timezone)
    return Runtime(
        settings=settings,
        auth=auth,
        calendar=calendar,
        audit=AuditLog(settings.audit_log_path),
        drafts=DraftStore(settings.drafts_dir),
        tasks=tasks,
    )


def get_runtime() -> Runtime:
    """Indirection point so tests can substitute a fake runtime."""
    return build_runtime()
