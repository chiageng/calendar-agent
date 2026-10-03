"""Google OAuth 2.0 sign-in for an installed (desktop) app, with PKCE and a persisted token file.

Google does not allow Calendar scopes in its device-code flow, so this uses the standard
authorization-code flow with a loopback redirect. Two modes:

* loopback: a one-shot local HTTP server on 127.0.0.1 receives the redirect (browser on this host)
* manual:   the user opens the URL anywhere and pastes the final ``http://127.0.0.1...`` URL back
            (works over SSH / headless)

Only the token file is persisted (mode 600). Tokens are never printed or logged.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import GoogleSettings
from .errors import AuthError, ConfigError, NetworkError, PermissionDeniedError
from .storage import write_private_text

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

LOOPBACK_HOST = "127.0.0.1"
MANUAL_REDIRECT_PORT = 8765  # any loopback port is accepted by Google for Desktop clients
_REFRESH_MARGIN_SECONDS = 60
PENDING_TTL_SECONDS = 60 * 60


def _decode_jwt_claims(id_token: str) -> dict[str, Any]:
    """Decode (without verifying) the payload of an ID token received directly from Google."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return dict(json.loads(base64.urlsafe_b64decode(payload)))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """Captures a single OAuth redirect."""

    result: dict[str, list[str]] = {}

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        type(self).result = parse_qs(urlparse(self.path).query)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Sign-in received. You can close this tab and return to the terminal.")

    def log_message(self, *_args: Any) -> None:  # silence request logging (URL has the code)
        return


class GoogleAuthenticator:
    def __init__(
        self,
        settings: GoogleSettings,
        token_path: Any,
        *,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._token_path = token_path
        self._session = session or requests.Session()
        self._clock = clock
        self._tokens: dict[str, Any] | None = None

    # -- token file ------------------------------------------------------------------
    def _load(self) -> dict[str, Any] | None:
        if self._tokens is None and self._token_path.exists():
            try:
                self._tokens = json.loads(self._token_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AuthError(
                    f"Token file at {self._token_path} is unreadable: {exc}",
                    hint="Delete the file and run 'login' again.",
                ) from exc
        return self._tokens

    def _store(self, response: dict[str, Any], *, previous: dict[str, Any] | None = None) -> None:
        tokens = dict(previous or {})
        tokens["access_token"] = response["access_token"]
        tokens["expires_at"] = self._clock() + float(response.get("expires_in", 3600))
        if response.get("refresh_token"):
            tokens["refresh_token"] = response["refresh_token"]
        if response.get("scope"):
            tokens["scope"] = response["scope"]
        if response.get("id_token"):
            claims = _decode_jwt_claims(response["id_token"])
            if claims.get("email"):
                tokens["email"] = claims["email"]
        self._tokens = tokens
        write_private_text(self._token_path, json.dumps(tokens))

    # -- token endpoint --------------------------------------------------------------
    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        payload = {
            "client_id": self._settings.client_id,
            "client_secret": self._settings.client_secret,
            **data,
        }
        try:
            response = self._session.post(TOKEN_URL, data=payload, timeout=30)
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"Could not reach Google's token endpoint: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or "access_token" not in body:
            raise self._describe_error(body, response.status_code)
        return dict(body)

    @staticmethod
    def _describe_error(body: dict[str, Any], status: int) -> AuthError | ConfigError:
        error = str(body.get("error", "")) or f"http_{status}"
        description = str(body.get("error_description", "")).strip()
        if error == "invalid_client":
            return ConfigError(
                "Google rejected the OAuth client (invalid_client).",
                hint="Check GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET against the Credentials "
                "page. The client must be of type 'Desktop app'.",
            )
        if error == "invalid_grant":
            return AuthError(
                "Your Google sign-in has expired or was revoked.",
                hint="Run 'login' again. While the OAuth app is in 'Testing' status, Google "
                "expires refresh tokens after 7 days.",
            )
        if error in {"access_denied", "admin_policy_enforced"}:
            return AuthError(
                "Google denied access to this account.",
                hint="Add your Google account under 'Test users' on the OAuth consent screen, "
                "or publish the app.",
            )
        if error == "invalid_scope":
            return ConfigError(
                "Google rejected the requested scopes.",
                hint="Check GOOGLE_SCOPES and that the Google Calendar API is enabled.",
            )
        detail = f"{error}: {description}" if description else error
        return AuthError(f"Google sign-in failed ({detail}).")

    # -- public API ------------------------------------------------------------------
    def signed_in_email(self) -> str | None:
        tokens = self._load()
        return tokens.get("email") if tokens else None

    # -- sign-in: begin / complete -------------------------------------------------------
    @property
    def _pending_path(self) -> Any:
        return self._token_path.with_name("google_login_pending.json")

    def begin_login(self, *, redirect_uri: str) -> str:
        """Create PKCE material + state, persist them (mode 600) and return the consent URL."""
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(24)
        pending = {
            "verifier": verifier,
            "state": state,
            "redirect_uri": redirect_uri,
            "created_at": self._clock(),
        }
        write_private_text(self._pending_path, json.dumps(pending))
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._settings.full_scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",  # ask for a refresh token
            "prompt": "consent",  # always issue a refresh token, even on re-login
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def complete_login(self, redirected_url_or_query: dict[str, list[str]] | str) -> dict[str, Any]:
        """Finish the flow from the redirect (URL string or parsed query). Returns ID claims."""
        if not self._pending_path.exists():
            raise AuthError(
                "No sign-in is in progress.",
                hint="Run: uv run outlook-calendar login --manual --no-wait",
            )
        pending = json.loads(self._pending_path.read_text(encoding="utf-8"))
        if self._clock() - float(pending.get("created_at", 0)) > PENDING_TTL_SECONDS:
            self._pending_path.unlink(missing_ok=True)
            raise AuthError("The sign-in attempt expired.", hint="Run 'login' again.")

        if isinstance(redirected_url_or_query, str):
            query = parse_qs(urlparse(redirected_url_or_query.strip()).query)
        else:
            query = redirected_url_or_query
        if query.get("error"):
            raise AuthError(f"Google returned an error during sign-in: {query['error'][0]}")
        code = (query.get("code") or [None])[0]
        if not code:
            raise AuthError(
                "No authorization code was found in that URL.",
                hint="Paste the complete URL from the browser address bar, starting with "
                "http://127.0.0.1",
            )
        if (query.get("state") or [None])[0] != pending["state"]:
            raise AuthError("Sign-in state mismatch; possible tampering. Please try again.")

        response = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": pending["verifier"],
                "redirect_uri": pending["redirect_uri"],
            }
        )
        self._pending_path.unlink(missing_ok=True)
        self._ensure_calendar_scope_granted(str(response.get("scope", "")))
        self._store(response)
        return _decode_jwt_claims(response.get("id_token", ""))

    def _missing_calendar_scopes(self, granted: str) -> list[str]:
        """Required calendar scopes not covered by the grant (broader scopes count as covering)."""
        granted_set = set(granted.split())
        base = "https://www.googleapis.com/auth/"

        def covered(scope: str) -> bool:
            if scope in granted_set or base + "calendar" in granted_set:
                return True
            if scope.endswith(".readonly") and scope.removesuffix(".readonly") in granted_set:
                return True
            return scope == base + "calendar.calendarlist.readonly" and (
                base + "calendar.readonly" in granted_set
            )

        return [s for s in self._settings.full_scopes if "/auth/calendar" in s and not covered(s)]

    def _ensure_calendar_scope_granted(self, granted: str) -> None:
        """Users can untick individual permissions; refuse a token without calendar access."""
        missing = self._missing_calendar_scopes(granted)
        if missing:
            self._token_path.unlink(missing_ok=True)
            raise PermissionDeniedError(
                "Google signed you in but did not grant calendar access: the calendar "
                "permission was left unticked on the consent screen.",
                hint="Run 'login' again. On the Google page that lists permissions, tick the "
                'calendar checkbox ("View and edit events on all your calendars") before '
                "clicking Continue.",
            )

    def login(
        self,
        echo: Callable[[str], None],
        *,
        manual: bool = False,
        wait: bool = True,
        reader: Callable[[str], str] = input,
        open_browser: bool = True,
    ) -> dict[str, Any]:
        """Run the authorization-code flow. Returns ID-token claims (email), never tokens.

        ``manual`` prints the URL and asks for the redirected URL; with ``wait=False`` it stops
        after printing the URL so ``complete_login`` can be called later (``login-complete``).
        """
        server: http.server.HTTPServer | None = None
        if manual:
            redirect_uri = f"http://{LOOPBACK_HOST}:{MANUAL_REDIRECT_PORT}/"
        else:
            try:
                server = http.server.HTTPServer((LOOPBACK_HOST, 0), _RedirectHandler)
            except OSError as exc:
                raise AuthError(f"Could not open a loopback port for sign-in: {exc}") from exc
            redirect_uri = f"http://{LOOPBACK_HOST}:{server.server_address[1]}/"

        url = self.begin_login(redirect_uri=redirect_uri)
        echo(
            "Open this URL in a browser and sign in with the Google account whose calendar "
            "you want to manage (copy the WHOLE line; it is one long URL):"
        )
        echo("")
        echo(url)
        echo("")
        try:
            if manual:
                echo(
                    "After approving, the browser will try to open a 127.0.0.1 address and show "
                    "a connection error. That is expected. Copy the FULL URL from the address bar."
                )
                if not wait:
                    echo("Then run:  uv run outlook-calendar login-complete 'http://127.0.0.1:...'")
                    return {}
                pasted = reader("Paste the redirected URL: ")
                return self.complete_login(pasted)
            if open_browser:
                with contextlib.suppress(Exception):  # best effort only
                    webbrowser.open(url, new=2)
            echo("Waiting for the browser to complete sign-in (Ctrl+C to abort)...")
            assert server is not None
            _RedirectHandler.result = {}
            server.timeout = 300
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            thread.join(timeout=300)
            query = dict(_RedirectHandler.result)
        finally:
            if server is not None:
                server.server_close()
        if not query:
            raise AuthError(
                "No authorization code was received.",
                hint="Try again. Over SSH use: uv run outlook-calendar login --manual",
            )
        return self.complete_login(query)

    def acquire_token(self) -> str:
        """Return a valid access token, refreshing silently when needed. Never prints it."""
        tokens = self._load()
        if not tokens or not tokens.get("refresh_token"):
            raise AuthError("Not signed in.", hint="Run: uv run outlook-calendar login")
        granted = str(tokens.get("scope", ""))
        if granted and self._missing_calendar_scopes(granted):
            raise AuthError(
                "Your saved sign-in predates a permission this version needs.",
                hint="Run 'login' again (Google will show the new permission as an extra "
                "checkbox).",
            )
        if tokens.get("expires_at", 0) - _REFRESH_MARGIN_SECONDS > self._clock():
            return str(tokens["access_token"])
        response = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
        )
        self._store(response, previous=tokens)
        assert self._tokens is not None
        return str(self._tokens["access_token"])

    def userinfo(self) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.acquire_token()}"}
        try:
            response = self._session.get(USERINFO_URL, headers=headers, timeout=30)
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"Could not reach Google userinfo endpoint: {exc}") from exc
        if response.status_code >= 400:
            raise AuthError("Google rejected the access token.", hint="Run 'login' again.")
        return dict(response.json())

    def logout(self) -> int:
        """Revoke the refresh token (best effort) and delete the token file."""
        tokens = self._load()
        removed = 0
        if tokens:
            token = tokens.get("refresh_token") or tokens.get("access_token")
            if token:  # best effort; local deletion proceeds regardless
                with contextlib.suppress(requests.exceptions.RequestException):
                    self._session.post(REVOKE_URL, data={"token": token}, timeout=15)
            removed = 1
        if self._token_path.exists():
            self._token_path.unlink()
        self._tokens = None
        return removed
