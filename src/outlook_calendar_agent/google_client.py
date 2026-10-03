"""Google Calendar REST client: ``nextPageToken`` pagination and Google error mapping."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import requests

from .errors import (
    AgentError,
    AuthError,
    GraphApiError,
    NotFoundError,
    PermissionDeniedError,
    StaleDraftError,
)
from .http_client import JsonApiClient, JsonDict

GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


class GoogleClient(JsonApiClient):
    service_name = "Google Calendar API"

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str = GOOGLE_CALENDAR_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        max_pages: int = 10,
    ) -> None:
        super().__init__(
            token_provider, base_url=base_url, session=session, timeout=timeout, max_pages=max_pages
        )

    def get_all(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> list[JsonDict]:
        """Follow ``nextPageToken`` up to ``max_pages`` pages; return the combined ``items``."""
        items: list[JsonDict] = []
        page_params = dict(params or {})
        for _ in range(self._max_pages):
            page = self.request("GET", path, params=page_params, headers=headers) or {}
            items.extend(page.get("items", []))
            token = page.get("nextPageToken")
            if not token:
                break
            page_params["pageToken"] = str(token)
        return items

    def error_for(self, response: requests.Response) -> AgentError:
        status = response.status_code
        reason, message = "", ""
        try:
            error = response.json().get("error", {})
            message = str(error.get("message", "")).strip()
            errors = error.get("errors") or []
            reason = str(errors[0].get("reason", "")) if errors else str(error.get("status", ""))
        except (ValueError, AttributeError, IndexError):
            message = response.text[:300]
        detail = f"{status} {reason}: {message}".strip(": ")

        if status == 401:
            return AuthError(
                f"Google rejected the access token ({detail}).",
                hint="Run 'login' again. Tokens for apps in 'Testing' status expire after 7 days.",
            )
        if status == 403 and reason in {"rateLimitExceeded", "userRateLimitExceeded"}:
            return GraphApiError(
                f"Google Calendar is rate limiting requests ({detail}).",
                status=status,
                code=reason,
                hint="Wait a moment and retry.",
            )
        if status == 403 and reason in {"accessNotConfigured", "SERVICE_DISABLED"}:
            return PermissionDeniedError(
                f"The Google Calendar API is not enabled for this project ({detail}).",
                hint="Google Cloud console → APIs & Services → Library → Google Calendar API "
                "→ Enable, then retry.",
            )
        if status == 403:
            return PermissionDeniedError(
                f"Google denied the request ({detail}).",
                hint="The granted scope is insufficient. Reads need calendar.events.readonly; "
                "writes need calendar.events. Update GOOGLE_SCOPES and run 'login' again.",
            )
        if status in {404, 410}:
            return NotFoundError(
                f"Event not found ({detail}).",
                status=status,
                code=reason,
                hint="The event may have been deleted or the ID may be wrong.",
            )
        if status == 412:
            return StaleDraftError(
                f"The event changed since the draft was prepared ({detail}).",
                hint="Discard this draft and prepare a new one from the current event.",
            )
        if status == 429:
            return GraphApiError(
                f"Google Calendar is throttling requests ({detail}).",
                status=status,
                code=reason,
                hint="Wait a moment and retry.",
            )
        return GraphApiError(f"Google Calendar API error ({detail}).", status=status, code=reason)
