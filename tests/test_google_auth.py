"""Google OAuth: manual login, PKCE/state checks, silent refresh, error mapping, file modes."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from outlook_calendar_agent.config import GoogleSettings
from outlook_calendar_agent.errors import AuthError, ConfigError, PermissionDeniedError
from outlook_calendar_agent.google_auth import TOKEN_URL, GoogleAuthenticator

SETTINGS = GoogleSettings(
    client_id="123-abc.apps.googleusercontent.com",
    client_secret="not-a-real-secret",
    scopes=("calendar.events",),
)


def _id_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).rstrip(b"=")
    return f"hdr.{payload.decode()}.sig"


class Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> dict:
        return self._body


class FakeSession:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.next_response = Response(
            200,
            {
                "access_token": "AT-1",
                "refresh_token": "RT-1",
                "expires_in": 3600,
                "id_token": _id_token("me@example.com"),
                "scope": "openid email https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.calendarlist.readonly",
            },
        )

    def post(self, url: str, data: dict, timeout: float) -> Response:
        self.posts.append((url, dict(data)))
        return self.next_response

    def get(self, *a, **k):  # pragma: no cover - not used here
        raise AssertionError


def _auth(tmp_path: Path, session: FakeSession, now: float = 1_000_000.0) -> GoogleAuthenticator:
    return GoogleAuthenticator(
        SETTINGS, tmp_path / "state" / "google_token.json", session=session, clock=lambda: now
    )


def test_manual_login_exchanges_code_with_pkce_and_stores_tokens(tmp_path: Path) -> None:
    session = FakeSession()
    auth = _auth(tmp_path, session)
    printed: list[str] = []

    def reader(prompt: str) -> str:
        url = next(line for line in printed if line.startswith("https://accounts.google.com"))
        q = parse_qs(urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"]
        assert q["access_type"] == ["offline"]
        assert "https://www.googleapis.com/auth/calendar.events" in q["scope"][0]
        assert q["redirect_uri"] == ["http://127.0.0.1:8765/"]
        return f"http://127.0.0.1:8765/?state={q['state'][0]}&code=AUTHCODE&scope=x"

    claims = auth.login(printed.append, manual=True, reader=reader)
    assert claims["email"] == "me@example.com"
    url, data = session.posts[-1]
    assert url == TOKEN_URL
    assert data["grant_type"] == "authorization_code" and data["code"] == "AUTHCODE"
    assert data["client_secret"] == "not-a-real-secret" and "code_verifier" in data
    token_file = tmp_path / "state" / "google_token.json"
    assert oct(token_file.stat().st_mode & 0o777) == "0o600"
    assert auth.signed_in_email() == "me@example.com"
    # Nothing printed resembles a token.
    assert not any("AT-1" in line or "RT-1" in line for line in printed)


def test_manual_login_rejects_state_mismatch(tmp_path: Path) -> None:
    session = FakeSession()
    auth = _auth(tmp_path, session)
    with pytest.raises(AuthError, match="state mismatch"):
        auth.login(
            lambda _m: None,
            manual=True,
            reader=lambda _p: "http://127.0.0.1:8765/?state=bad&code=X",
        )
    assert session.posts == []


def test_manual_login_surfaces_google_error(tmp_path: Path) -> None:
    auth = _auth(tmp_path, FakeSession())
    with pytest.raises(AuthError, match="access_denied"):
        auth.login(
            lambda _m: None,
            manual=True,
            reader=lambda _p: "http://127.0.0.1:8765/?error=access_denied",
        )


def test_acquire_token_uses_cache_then_refreshes(tmp_path: Path) -> None:
    session = FakeSession()
    auth = _auth(tmp_path, session)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "google_token.json").write_text(
        json.dumps(
            {"access_token": "AT-old", "refresh_token": "RT-1", "expires_at": 1_000_000.0 + 3600}
        )
    )
    assert auth.acquire_token() == "AT-old"  # still valid: no network call
    assert session.posts == []

    expired = _auth(tmp_path, session, now=1_000_000.0 + 4000)
    session.next_response = Response(200, {"access_token": "AT-new", "expires_in": 3600})
    assert expired.acquire_token() == "AT-new"
    url, data = session.posts[-1]
    assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "RT-1"
    stored = json.loads((tmp_path / "state" / "google_token.json").read_text())
    assert stored["refresh_token"] == "RT-1" and stored["access_token"] == "AT-new"


def test_not_signed_in_and_error_mapping(tmp_path: Path) -> None:
    session = FakeSession()
    auth = _auth(tmp_path, session)
    with pytest.raises(AuthError, match="Not signed in"):
        auth.acquire_token()
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "google_token.json").write_text(
        json.dumps({"access_token": "x", "refresh_token": "RT", "expires_at": 0})
    )
    session.next_response = Response(400, {"error": "invalid_grant"})
    with pytest.raises(AuthError, match="expired or was revoked") as info:
        _auth(tmp_path, session).acquire_token()
    assert "7 days" in (info.value.hint or "")
    session.next_response = Response(401, {"error": "invalid_client"})
    with pytest.raises(ConfigError, match="invalid_client") as info:
        _auth(tmp_path, session).acquire_token()
    assert "Desktop app" in (info.value.hint or "")


def test_logout_removes_file(tmp_path: Path) -> None:
    session = FakeSession()
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "state" / "google_token.json"
    path.write_text(json.dumps({"access_token": "x", "refresh_token": "RT", "expires_at": 0}))
    auth = _auth(tmp_path, session)
    assert auth.logout() == 1
    assert not path.exists()
    assert session.posts[-1][0].endswith("/revoke")


def test_two_step_login_via_pending_file(tmp_path: Path) -> None:
    session = FakeSession()
    first = _auth(tmp_path, session)
    printed: list[str] = []
    assert first.login(printed.append, manual=True, wait=False) == {}
    url = next(line for line in printed if line.startswith("https://accounts.google.com"))
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"]
    pending = tmp_path / "state" / "google_login_pending.json"
    assert oct(pending.stat().st_mode & 0o777) == "0o600"

    # A fresh process (new authenticator instance) completes the flow.
    second = _auth(tmp_path, session)
    with pytest.raises(AuthError, match="state mismatch"):
        second.complete_login("http://127.0.0.1:8765/?state=wrong&code=X")
    claims = second.complete_login(f"http://127.0.0.1:8765/?state={q['state'][0]}&code=AUTH")
    assert claims["email"] == "me@example.com"
    assert not pending.exists()
    assert session.posts[-1][1]["redirect_uri"] == "http://127.0.0.1:8765/"


def test_complete_login_without_pending_or_expired(tmp_path: Path) -> None:
    session = FakeSession()
    with pytest.raises(AuthError, match="No sign-in is in progress"):
        _auth(tmp_path, session).complete_login("http://127.0.0.1:8765/?code=x&state=y")
    stale = _auth(tmp_path, session, now=1_000.0)
    stale.begin_login(redirect_uri="http://127.0.0.1:8765/")
    later = _auth(tmp_path, session, now=1_000.0 + 61 * 60)
    with pytest.raises(AuthError, match="expired"):
        later.complete_login("http://127.0.0.1:8765/?code=x&state=y")


def test_login_refuses_token_without_calendar_scope(tmp_path: Path) -> None:
    session = FakeSession()
    session.next_response = Response(
        200,
        {
            "access_token": "AT-1",
            "refresh_token": "RT-1",
            "expires_in": 3600,
            "id_token": _id_token("me@example.com"),
            "scope": "https://www.googleapis.com/auth/userinfo.email openid",
        },
    )
    auth = _auth(tmp_path, session)
    url = auth.begin_login(redirect_uri="http://127.0.0.1:8765/")
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(PermissionDeniedError, match="unticked"):
        auth.complete_login(f"http://127.0.0.1:8765/?state={state}&code=AUTH")
    assert not (tmp_path / "state" / "google_token.json").exists()


def test_cached_token_missing_new_scope_asks_for_relogin(tmp_path: Path) -> None:
    session = FakeSession()
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "google_token.json").write_text(
        json.dumps(
            {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_at": 9e12,
                "scope": "openid email https://www.googleapis.com/auth/calendar.events",
            }
        )
    )
    with pytest.raises(AuthError, match="predates") as info:
        _auth(tmp_path, session).acquire_token()
    assert "login" in (info.value.hint or "")
    assert session.posts == []
