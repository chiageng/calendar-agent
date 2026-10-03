"""Shared JSON-over-HTTPS client base. Tokens are only ever placed in the Authorization header."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import requests

from .errors import AgentError, GraphApiError, NetworkError

JsonDict = dict[str, Any]


class JsonApiClient:
    """Minimal REST client. Subclasses implement ``error_for`` (status → AgentError)."""

    service_name = "the API"

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        max_pages: int = 10,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_pages = max_pages

    # -- verbs -----------------------------------------------------------------------
    def get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonDict:
        return self.request("GET", path, params=params, headers=headers) or {}

    def post(
        self,
        path: str,
        *,
        json: JsonDict,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonDict:
        return self.request("POST", path, params=params, json=json, headers=headers) or {}

    def patch(
        self,
        path: str,
        *,
        json: JsonDict,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonDict:
        return self.request("PATCH", path, params=params, json=json, headers=headers) or {}

    def delete(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.request("DELETE", path, params=params, headers=headers)

    # -- internals -------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: JsonDict | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonDict | None:
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        request_headers = {
            "Authorization": f"Bearer {self._token_provider()}",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)
        try:
            response = self._session.request(
                method,
                url,
                params=dict(params) if params else None,
                json=json,
                headers=request_headers,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise NetworkError(
                f"{self.service_name} timed out after {self._timeout:.0f}s."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise NetworkError(
                f"Could not connect to {self.service_name}. Check your network connection."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"Request to {self.service_name} failed: {exc}") from exc

        if response.status_code >= 400:
            raise self.error_for(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            body = response.json()
        except ValueError as exc:
            raise GraphApiError(
                f"{self.service_name} returned a non-JSON response.",
                status=response.status_code,
            ) from exc
        return body if isinstance(body, dict) else {"value": body}

    def error_for(self, response: requests.Response) -> AgentError:  # pragma: no cover
        raise NotImplementedError
