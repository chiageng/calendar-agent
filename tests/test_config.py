from pathlib import Path

import pytest

from outlook_calendar_agent.config import load_settings
from outlook_calendar_agent.errors import ConfigError

GUID = "11111111-2222-3333-4444-555555555555"
GOOGLE_ID = "123456789-abcdefg.apps.googleusercontent.com"


def _google_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "GOOGLE_CLIENT_ID": GOOGLE_ID,
        "GOOGLE_CLIENT_SECRET": "not-a-real-secret",
        "OUTLOOK_AGENT_STATE_DIR": str(tmp_path / "state"),
    }
    env.update(overrides)
    return env


def _ms_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "CALENDAR_PROVIDER": "microsoft",
        "MS_CLIENT_ID": GUID,
        "OUTLOOK_AGENT_STATE_DIR": str(tmp_path / "state"),
    }
    env.update(overrides)
    return env


def test_google_is_default_provider(tmp_path: Path) -> None:
    settings = load_settings(_google_env(tmp_path))
    assert settings.provider == "google"
    assert settings.google is not None and settings.microsoft is None
    assert settings.scopes == ("calendar.events",)
    assert settings.can_write
    assert settings.google.full_scopes == (
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.events",
    )
    assert settings.token_cache_path.name == "google_token.json"
    assert settings.timezone_name == "Asia/Singapore"
    assert oct(settings.state_dir.stat().st_mode & 0o777) == "0o700"


def test_google_read_only_and_scope_validation(tmp_path: Path) -> None:
    ro = load_settings(_google_env(tmp_path, GOOGLE_SCOPES="calendar.events.readonly"))
    assert not ro.can_write and "GOOGLE_SCOPES=calendar.events" in ro.write_scope_hint
    full = load_settings(
        _google_env(tmp_path, GOOGLE_SCOPES="https://www.googleapis.com/auth/calendar.events")
    )
    assert full.scopes == ("calendar.events",)
    with pytest.raises(ConfigError, match="Unsupported scope"):
        load_settings(_google_env(tmp_path, GOOGLE_SCOPES="gmail.readonly"))


def test_google_missing_or_invalid_credentials(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="GOOGLE_CLIENT_ID is not set"):
        load_settings({"OUTLOOK_AGENT_STATE_DIR": str(tmp_path)})
    with pytest.raises(ConfigError, match="does not look like"):
        load_settings(_google_env(tmp_path, GOOGLE_CLIENT_ID="abc"))
    with pytest.raises(ConfigError, match="GOOGLE_CLIENT_SECRET is not set"):
        load_settings(_google_env(tmp_path, GOOGLE_CLIENT_SECRET=""))


def test_invalid_provider(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="CALENDAR_PROVIDER"):
        load_settings(_google_env(tmp_path, CALENDAR_PROVIDER="icloud"))


def test_microsoft_defaults(tmp_path: Path) -> None:
    settings = load_settings(_ms_env(tmp_path))
    assert settings.provider == "microsoft" and settings.microsoft is not None
    assert settings.microsoft.tenant_id == "common"
    assert settings.scopes == ("User.Read", "Calendars.ReadWrite")
    assert settings.can_write
    assert settings.microsoft.authority == "https://login.microsoftonline.com/common"
    assert settings.token_cache_path.name == "token_cache.json"


def test_microsoft_validation(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="MS_CLIENT_ID is not set"):
        load_settings({"CALENDAR_PROVIDER": "microsoft", "OUTLOOK_AGENT_STATE_DIR": str(tmp_path)})
    with pytest.raises(ConfigError, match="not a valid GUID"):
        load_settings(_ms_env(tmp_path, MS_CLIENT_ID="my-secret-value"))
    with pytest.raises(ConfigError, match="MS_TENANT_ID"):
        load_settings(_ms_env(tmp_path, MS_TENANT_ID="not a tenant"))
    ro = load_settings(_ms_env(tmp_path, MS_SCOPES="User.Read Calendars.Read offline_access"))
    assert ro.scopes == ("User.Read", "Calendars.Read") and not ro.can_write
    with pytest.raises(ConfigError, match="Unsupported scope"):
        load_settings(_ms_env(tmp_path, MS_SCOPES="Mail.ReadWrite"))


def test_xdg_state_home(tmp_path: Path) -> None:
    env = {
        "GOOGLE_CLIENT_ID": GOOGLE_ID,
        "GOOGLE_CLIENT_SECRET": "x",
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
    }
    settings = load_settings(env)
    assert settings.state_dir == tmp_path / "xdg" / "outlook-calendar-agent"
