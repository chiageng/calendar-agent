"""MSAL device-code sign-in with a persistent, owner-only token cache.

Only delegated permissions are used. No client secret exists; this is a public client.
Tokens are never logged or printed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import msal
import requests

from .config import MicrosoftSettings
from .errors import AuthError, ConfigError, NetworkError
from .storage import write_private_text

# Known AADSTS error codes mapped to actionable guidance.
_AADSTS_HINTS: dict[str, tuple[type[AuthError] | type[ConfigError], str, str]] = {
    "AADSTS700016": (
        ConfigError,
        "The application (client) ID was not found in the selected tenant.",
        "Check MS_CLIENT_ID and MS_TENANT_ID. For personal accounts the registration must allow "
        "personal Microsoft accounts; for a work tenant set MS_TENANT_ID to that tenant.",
    ),
    "AADSTS90002": (
        ConfigError,
        "The tenant in MS_TENANT_ID does not exist.",
        "Use 'common', 'organizations', 'consumers' or your directory (tenant) ID.",
    ),
    "AADSTS7000218": (
        ConfigError,
        "Public client flows are disabled for this app registration.",
        "In Entra: App registration → Authentication → Advanced settings → "
        "'Allow public client flows' = Yes. Do not create a client secret.",
    ),
    "AADSTS65001": (
        AuthError,
        "Consent to the requested permissions was not granted or was revoked.",
        "Run 'login' again and accept the consent prompt. Verify the delegated permissions in "
        "Entra → API permissions.",
    ),
    "AADSTS50020": (
        AuthError,
        "This account cannot sign in to the configured tenant.",
        "A personal account needs MS_TENANT_ID=common or consumers; a work account may require "
        "your organisation's tenant ID.",
    ),
    "AADSTS50076": (
        AuthError,
        "Multi-factor authentication is required.",
        "Complete the MFA prompt in the browser during device-code sign-in.",
    ),
    "AADSTS70011": (
        ConfigError,
        "The requested scopes are invalid for this application.",
        "Check MS_SCOPES; the delegated permissions must also be added in Entra.",
    ),
}


def _describe_msal_error(result: dict[str, Any], default: str) -> AuthError | ConfigError:
    error = str(result.get("error", "")) or "unknown_error"
    description = str(result.get("error_description", "")).strip()
    for code, (exc_type, message, hint) in _AADSTS_HINTS.items():
        if code in description:
            return exc_type(f"{message} ({code})", hint=hint)
    if error in {"invalid_grant", "interaction_required", "consent_required"}:
        return AuthError(
            "Your sign-in has expired or requires fresh consent.",
            hint="Run: uv run outlook-calendar login",
        )
    if error == "invalid_client":
        return ConfigError(
            "Microsoft rejected the client configuration (invalid_client).",
            hint="Verify MS_CLIENT_ID and that public client flows are enabled.",
        )
    first_line = description.splitlines()[0] if description else ""
    detail = f"{error}: {first_line}" if first_line else error
    return AuthError(f"{default} ({detail})")


class Authenticator:
    """Wraps MSAL's PublicClientApplication with a persisted SerializableTokenCache."""

    def __init__(self, settings: MicrosoftSettings, token_path: Path) -> None:
        self._settings = settings
        self._token_path = token_path
        self._cache = msal.SerializableTokenCache()
        self._app: msal.PublicClientApplication | None = None
        self._load_cache()

    # -- cache -------------------------------------------------------------------------
    def _load_cache(self) -> None:
        path = self._token_path
        if path.exists():
            try:
                self._cache.deserialize(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AuthError(
                    f"Token cache at {path} is unreadable: {exc}",
                    hint="Delete the file and run 'login' again.",
                ) from exc

    def _persist_cache(self) -> None:
        if self._cache.has_state_changed:
            write_private_text(self._token_path, self._cache.serialize())

    # -- MSAL app ----------------------------------------------------------------------
    @property
    def app(self) -> msal.PublicClientApplication:
        if self._app is None:
            try:
                self._app = msal.PublicClientApplication(
                    self._settings.client_id,
                    authority=self._settings.authority,
                    token_cache=self._cache,
                )
            except requests.exceptions.RequestException as exc:
                raise NetworkError(f"Could not reach Microsoft identity platform: {exc}") from exc
            except ValueError as exc:
                raise ConfigError(
                    f"Invalid authority for MS_TENANT_ID={self._settings.tenant_id!r}: {exc}",
                    hint="Use 'common', 'organizations', 'consumers' or your tenant ID.",
                ) from exc
        return self._app

    # -- public API --------------------------------------------------------------------
    def signed_in_email(self) -> str | None:
        accounts = self.app.get_accounts()
        return accounts[0].get("username") if accounts else None

    def login(self, echo: Callable[[str], None]) -> dict[str, Any]:
        """Run the device-code flow. Returns the ID token claims (never the tokens)."""
        scopes = list(self._settings.scopes)
        try:
            flow = self.app.initiate_device_flow(scopes=scopes)
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"Could not start device-code sign-in: {exc}") from exc
        if "user_code" not in flow:
            raise _describe_msal_error(flow, "Could not start device-code sign-in")

        echo(flow["message"])
        try:
            result = self.app.acquire_token_by_device_flow(flow)
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"Network error while waiting for sign-in: {exc}") from exc
        finally:
            self._persist_cache()

        if "access_token" not in result:
            raise _describe_msal_error(result, "Sign-in failed")
        return dict(result.get("id_token_claims") or {})

    def acquire_token(self) -> str:
        """Return an access token silently (cache or refresh token). Never prints it."""
        accounts = self.app.get_accounts()
        if not accounts:
            raise AuthError("Not signed in.", hint="Run: uv run outlook-calendar login")
        try:
            result = self.app.acquire_token_silent(list(self._settings.scopes), account=accounts[0])
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"Could not refresh the access token: {exc}") from exc
        finally:
            self._persist_cache()
        if not result:
            raise AuthError(
                "No cached token matches the configured scopes.",
                hint="Run 'login' again (required after changing MS_SCOPES, e.g. adding "
                "Calendars.ReadWrite).",
            )
        if "access_token" not in result:
            raise _describe_msal_error(result, "Could not acquire an access token")
        return str(result["access_token"])

    def logout(self) -> int:
        """Remove all cached accounts and tokens. Returns the number of accounts removed."""
        accounts = self.app.get_accounts()
        for account in accounts:
            self.app.remove_account(account)
        self._persist_cache()
        path = self._token_path
        if path.exists():
            path.unlink()
        return len(accounts)
