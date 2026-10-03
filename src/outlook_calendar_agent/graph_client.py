"""Microsoft Graph REST client: pagination via ``@odata.nextLink`` and Graph error mapping."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import requests

from .errors import (
    AgentError,
    AuthError,
    GraphApiError,
    NotFoundError,
    PermissionDeniedError,
)
from .http_client import JsonApiClient, JsonDict

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphClient(JsonApiClient):
    service_name = "Microsoft Graph"

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str = GRAPH_BASE_URL,
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
        """Follow ``@odata.nextLink`` up to ``max_pages`` pages; return the combined ``value``."""
        items: list[JsonDict] = []
        url: str | None = path
        page_params: Mapping[str, str] | None = params
        for _ in range(self._max_pages):
            if url is None:
                break
            page = self.request("GET", url, params=page_params, headers=headers) or {}
            items.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
            page_params = None  # nextLink already carries the query string
        return items

    def error_for(self, response: requests.Response) -> AgentError:
        status = response.status_code
        code, message = "", ""
        try:
            error = response.json().get("error", {})
            code = str(error.get("code", ""))
            message = str(error.get("message", "")).strip()
        except ValueError:
            message = response.text[:300]
        detail = f"{status} {code}: {message}".strip(": ")

        if status == 401:
            return AuthError(
                f"Microsoft Graph rejected the access token ({detail}).",
                hint="Run 'login' again. If this persists, check that consent was granted.",
            )
        if status == 403:
            return PermissionDeniedError(
                f"Microsoft Graph denied the request ({detail}).",
                hint="A delegated permission is missing or not consented. Reads need "
                "Calendars.Read; writes need Calendars.ReadWrite. Add it in Entra → API "
                "permissions, update MS_SCOPES and run 'login' again.",
            )
        if status == 404:
            return NotFoundError(
                f"Resource not found ({detail}).",
                status=status,
                code=code,
                hint="The event may have been deleted or the ID may be wrong.",
            )
        if status == 429:
            retry = response.headers.get("Retry-After", "a few")
            return GraphApiError(
                f"Microsoft Graph is throttling requests ({detail}).",
                status=status,
                code=code,
                hint=f"Retry after {retry} seconds.",
            )
        if status == 412:
            return GraphApiError(
                f"The event changed since it was read ({detail}).",
                status=status,
                code=code,
                hint="Prepare a fresh draft and try again.",
            )
        return GraphApiError(f"Microsoft Graph error ({detail}).", status=status, code=code)
