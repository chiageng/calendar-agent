"""Environment-driven configuration. Validated once at startup; no network access here.

Two providers are supported. ``google`` is the default; ``microsoft`` (Graph) remains available.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from .errors import ConfigError
from .storage import ensure_private_dir
from .timeutil import DEFAULT_TZ_NAME

APP_DIR_NAME = "outlook-calendar-agent"
Provider = Literal["google", "microsoft"]

# ---- Google ---------------------------------------------------------------------------------
GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"
GOOGLE_SCOPE_PREFIX = "https://www.googleapis.com/auth/"
GOOGLE_READ_SCOPE = "calendar.events.readonly"
GOOGLE_WRITE_SCOPE = "calendar.events"
GOOGLE_LIST_SCOPE = "calendar.calendarlist.readonly"  # list calendars; always requested
GOOGLE_TASKS_SCOPE = "tasks.readonly"  # optional: read Google Tasks (needs Tasks API enabled)
GOOGLE_ALLOWED_SCOPES = frozenset(
    {
        GOOGLE_READ_SCOPE,
        GOOGLE_WRITE_SCOPE,
        "calendar.readonly",
        GOOGLE_LIST_SCOPE,
        GOOGLE_TASKS_SCOPE,
    }
)
GOOGLE_IDENTITY_SCOPES: tuple[str, ...] = ("openid", "email")  # for whoami; always requested
GOOGLE_DEFAULT_SCOPES: tuple[str, ...] = (GOOGLE_WRITE_SCOPE, GOOGLE_TASKS_SCOPE)
_GOOGLE_CLIENT_ID_RE = re.compile(r"^[0-9]+-[0-9a-z]+\.apps\.googleusercontent\.com$")

# ---- Microsoft --------------------------------------------------------------------------------
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
AUTHORITY_HOST = "https://login.microsoftonline.com"
READ_SCOPE = "Calendars.Read"
WRITE_SCOPE = "Calendars.ReadWrite"
DEFAULT_SCOPES: tuple[str, ...] = ("User.Read", WRITE_SCOPE)
RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})
ALLOWED_SCOPES = frozenset({"User.Read", READ_SCOPE, WRITE_SCOPE})
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_TENANT_ALIASES = frozenset({"common", "organizations", "consumers"})


@dataclass(frozen=True)
class GoogleSettings:
    client_id: str
    client_secret: str  # required by Google for desktop clients; never logged or printed
    scopes: tuple[str, ...]  # short names, e.g. "calendar.events"
    base_url: str = GOOGLE_CALENDAR_BASE_URL

    @property
    def full_scopes(self) -> tuple[str, ...]:
        calendar_scopes = tuple(dict.fromkeys((*self.scopes, GOOGLE_LIST_SCOPE)))
        return GOOGLE_IDENTITY_SCOPES + tuple(GOOGLE_SCOPE_PREFIX + s for s in calendar_scopes)

    @property
    def can_write(self) -> bool:
        return GOOGLE_WRITE_SCOPE in self.scopes

    @property
    def wants_tasks(self) -> bool:
        return GOOGLE_TASKS_SCOPE in self.scopes


@dataclass(frozen=True)
class MicrosoftSettings:
    client_id: str
    tenant_id: str
    scopes: tuple[str, ...]
    base_url: str = GRAPH_BASE_URL

    @property
    def authority(self) -> str:
        return f"{AUTHORITY_HOST}/{self.tenant_id}"

    @property
    def can_write(self) -> bool:
        return WRITE_SCOPE in self.scopes


@dataclass(frozen=True)
class Settings:
    provider: Provider
    timezone_name: str
    state_dir: Path
    google: GoogleSettings | None = None
    microsoft: MicrosoftSettings | None = None

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def token_cache_path(self) -> Path:
        name = "google_token.json" if self.provider == "google" else "token_cache.json"
        return self.state_dir / name

    @property
    def audit_log_path(self) -> Path:
        return self.state_dir / "audit.jsonl"

    @property
    def drafts_dir(self) -> Path:
        return self.state_dir / "drafts"

    @property
    def scopes(self) -> tuple[str, ...]:
        if self.provider == "google":
            assert self.google is not None
            return self.google.scopes
        assert self.microsoft is not None
        return self.microsoft.scopes

    @property
    def can_write(self) -> bool:
        if self.provider == "google":
            assert self.google is not None
            return self.google.can_write
        assert self.microsoft is not None
        return self.microsoft.can_write

    @property
    def write_scope_hint(self) -> str:
        if self.provider == "google":
            return (
                f"Set GOOGLE_SCOPES={GOOGLE_WRITE_SCOPE} (instead of {GOOGLE_READ_SCOPE}) "
                "and run 'login' again."
            )
        return (
            "Add the delegated permission Calendars.ReadWrite in Entra, set "
            "MS_SCOPES='User.Read Calendars.ReadWrite' and run 'login' again."
        )


def default_state_dir(env: Mapping[str, str]) -> Path:
    override = env.get("OUTLOOK_AGENT_STATE_DIR") or env.get("CALENDAR_AGENT_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = env.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / APP_DIR_NAME


def _split(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(s for s in raw.replace(",", " ").split() if s))


def _load_google(env: Mapping[str, str]) -> GoogleSettings:
    client_id = env.get("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise ConfigError(
            "GOOGLE_CLIENT_ID is not set.",
            hint="Create an OAuth client of type 'Desktop app' in Google Cloud console → "
            "APIs & Services → Credentials, then put its Client ID and Client secret in .env.",
        )
    if not _GOOGLE_CLIENT_ID_RE.match(client_id):
        raise ConfigError(
            "GOOGLE_CLIENT_ID does not look like a Google OAuth client ID.",
            hint="It should end with .apps.googleusercontent.com.",
        )
    client_secret = env.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_secret:
        raise ConfigError(
            "GOOGLE_CLIENT_SECRET is not set.",
            hint="Google requires the client secret of a Desktop-app OAuth client for the token "
            "exchange. Copy it from the Credentials page into .env (never commit .env).",
        )
    scopes = tuple(
        s.removeprefix(GOOGLE_SCOPE_PREFIX)
        for s in _split(env.get("GOOGLE_SCOPES", " ".join(GOOGLE_DEFAULT_SCOPES)))
        if s not in GOOGLE_IDENTITY_SCOPES
    )
    if not scopes:
        raise ConfigError(
            "GOOGLE_SCOPES must list a calendar scope.",
            hint=f"Default: GOOGLE_SCOPES={GOOGLE_WRITE_SCOPE}",
        )
    unknown = [s for s in scopes if s not in GOOGLE_ALLOWED_SCOPES]
    if unknown:
        raise ConfigError(
            f"Unsupported scope(s) in GOOGLE_SCOPES: {', '.join(unknown)}.",
            hint=f"Allowed: {', '.join(sorted(GOOGLE_ALLOWED_SCOPES))}.",
        )
    return GoogleSettings(
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        base_url=env.get("GOOGLE_CALENDAR_BASE_URL", GOOGLE_CALENDAR_BASE_URL).rstrip("/"),
    )


def _load_microsoft(env: Mapping[str, str]) -> MicrosoftSettings:
    client_id = env.get("MS_CLIENT_ID", "").strip()
    if not client_id:
        raise ConfigError(
            "MS_CLIENT_ID is not set.",
            hint="Copy .env.example to .env and paste the Application (client) ID from your "
            "Entra app registration. Never commit .env.",
        )
    if not _GUID_RE.match(client_id):
        raise ConfigError(
            "MS_CLIENT_ID is not a valid GUID.",
            hint="Use the 'Application (client) ID' from the app registration Overview page, "
            "not the Object ID or a client secret.",
        )
    tenant_id = env.get("MS_TENANT_ID", "common").strip() or "common"
    if not (
        tenant_id in _TENANT_ALIASES or _GUID_RE.match(tenant_id) or _DOMAIN_RE.match(tenant_id)
    ):
        raise ConfigError(
            f"MS_TENANT_ID={tenant_id!r} is invalid.",
            hint="Use 'common', 'organizations', 'consumers', a directory (tenant) GUID or a "
            "verified tenant domain such as contoso.onmicrosoft.com.",
        )
    scopes = tuple(
        s
        for s in _split(env.get("MS_SCOPES", " ".join(DEFAULT_SCOPES)))
        if s not in RESERVED_SCOPES
    )
    if not scopes:
        raise ConfigError(
            "MS_SCOPES must list at least one Microsoft Graph delegated scope.",
            hint=f"Default: MS_SCOPES={' '.join(DEFAULT_SCOPES)}",
        )
    unknown = [s for s in scopes if s not in ALLOWED_SCOPES]
    if unknown:
        raise ConfigError(
            f"Unsupported scope(s) in MS_SCOPES: {', '.join(unknown)}.",
            hint=f"This agent only uses delegated scopes: {', '.join(sorted(ALLOWED_SCOPES))}.",
        )
    return MicrosoftSettings(
        client_id=client_id,
        tenant_id=tenant_id,
        scopes=scopes,
        base_url=env.get("MS_GRAPH_BASE_URL", GRAPH_BASE_URL).rstrip("/"),
    )


def load_settings(
    environ: Mapping[str, str] | None = None, *, env_file: Path | None = None
) -> Settings:
    """Load settings from the process environment (and a local .env file when present)."""
    if environ is None:
        load_dotenv(dotenv_path=env_file, override=False)
        env: Mapping[str, str] = os.environ
    else:
        env = environ

    provider_raw = env.get("CALENDAR_PROVIDER", "google").strip().lower() or "google"
    if provider_raw not in {"google", "microsoft"}:
        raise ConfigError(
            f"CALENDAR_PROVIDER={provider_raw!r} is invalid.",
            hint="Use 'google' (default) or 'microsoft'.",
        )
    provider: Provider = "google" if provider_raw == "google" else "microsoft"

    timezone_name = (
        env.get("CALENDAR_AGENT_TIMEZONE") or env.get("OUTLOOK_AGENT_TIMEZONE") or DEFAULT_TZ_NAME
    ).strip() or DEFAULT_TZ_NAME
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"CALENDAR_AGENT_TIMEZONE={timezone_name!r} is not a valid IANA time zone."
        ) from exc

    state_dir = default_state_dir(env)
    try:
        ensure_private_dir(state_dir)
    except OSError as exc:
        raise ConfigError(f"Cannot create state directory {state_dir}: {exc}") from exc

    return Settings(
        provider=provider,
        timezone_name=timezone_name,
        state_dir=state_dir,
        google=_load_google(env) if provider == "google" else None,
        microsoft=_load_microsoft(env) if provider == "microsoft" else None,
    )
