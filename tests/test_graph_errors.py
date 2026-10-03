import pytest
import requests

from outlook_calendar_agent.errors import (
    AuthError,
    GraphApiError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
)
from outlook_calendar_agent.graph_client import GraphClient


class Response:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""
        self.headers = headers or {}
        self.text = ""

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class Session:
    def __init__(self, response=None, exc=None):
        self.response, self.exc = response, exc

    def request(self, *args, **kwargs):
        if self.exc:
            raise self.exc
        return self.response


def _client(session):
    return GraphClient(lambda: "tok", session=session)


@pytest.mark.parametrize(
    ("status", "code", "exc_type"),
    [
        (401, "InvalidAuthenticationToken", AuthError),
        (403, "ErrorAccessDenied", PermissionDeniedError),
        (404, "ErrorItemNotFound", NotFoundError),
        (429, "TooManyRequests", GraphApiError),
        (500, "InternalServerError", GraphApiError),
    ],
)
def test_status_mapping(status, code, exc_type):
    body = {"error": {"code": code, "message": "boom"}}
    with pytest.raises(exc_type) as info:
        _client(Session(Response(status, body))).get("/me")
    assert code in str(info.value)


def test_network_errors_are_wrapped():
    with pytest.raises(NetworkError):
        _client(Session(exc=requests.exceptions.ConnectionError())).get("/me")
    with pytest.raises(NetworkError):
        _client(Session(exc=requests.exceptions.Timeout())).get("/me")


def test_no_content_returns_none():
    assert _client(Session(Response(204))).delete("/me/events/x") is None
