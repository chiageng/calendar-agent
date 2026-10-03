import pytest

from outlook_calendar_agent.errors import (
    AuthError,
    GraphApiError,
    NotFoundError,
    PermissionDeniedError,
    StaleDraftError,
)
from outlook_calendar_agent.google_client import GoogleClient


class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""
        self.headers = {}
        self.text = ""

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.requests.append((method, url, params, headers))
        return self.responses.pop(0)


def _body(reason, message="boom"):
    return {"error": {"code": 0, "message": message, "errors": [{"reason": reason}]}}


@pytest.mark.parametrize(
    ("status", "reason", "exc_type", "needle"),
    [
        (401, "authError", AuthError, "7 days"),
        (403, "insufficientPermissions", PermissionDeniedError, "calendar.events"),
        (403, "accessNotConfigured", PermissionDeniedError, "not enabled"),
        (403, "rateLimitExceeded", GraphApiError, "rate limiting"),
        (404, "notFound", NotFoundError, "not found"),
        (410, "deleted", NotFoundError, "not found"),
        (412, "conditionNotMet", StaleDraftError, "changed since"),
        (429, "quotaExceeded", GraphApiError, "throttling"),
        (500, "backendError", GraphApiError, "backendError"),
    ],
)
def test_google_status_mapping(status, reason, exc_type, needle):
    client = GoogleClient(lambda: "tok", session=Session([Response(status, _body(reason))]))
    with pytest.raises(exc_type) as info:
        client.get("/calendars/primary/events/x")
    text = str(info.value) + " " + (info.value.hint or "")
    assert needle in text


def test_google_pagination_uses_page_token():
    session = Session(
        [
            Response(200, {"items": [1], "nextPageToken": "p2"}),
            Response(200, {"items": [2]}),
        ]
    )
    client = GoogleClient(lambda: "tok", session=session)
    items = client.get_all("/calendars/primary/events", params={"maxResults": "1"})
    assert items == [1, 2]
    assert session.requests[0][2] == {"maxResults": "1"}
    assert session.requests[1][2] == {"maxResults": "1", "pageToken": "p2"}
    assert session.requests[1][3]["Authorization"] == "Bearer tok"
