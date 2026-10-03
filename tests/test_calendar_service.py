from datetime import datetime, timedelta

import pytest

from outlook_calendar_agent.graph_calendar import GraphCalendarService as CalendarService
from outlook_calendar_agent.models import LIST_SELECT, EventDraft
from outlook_calendar_agent.timeutil import SGT

from .conftest import EVENT_ID, FakeGraphClient, graph_event


def test_list_events_uses_bounded_calendar_view_with_small_select(graph: FakeGraphClient) -> None:
    service = CalendarService(graph, SGT)  # type: ignore[arg-type]
    start = datetime(2026, 10, 7, 0, 0, tzinfo=SGT)
    events = service.list_events(start, start + timedelta(days=1))

    call = graph.calls[-1]
    assert call.method == "GET" and call.path == "/me/calendarView"
    assert call.params["startDateTime"] == "2026-10-06T16:00:00Z"
    assert call.params["endDateTime"] == "2026-10-07T16:00:00Z"
    assert call.params["$select"] == ",".join(LIST_SELECT)
    assert call.params["$orderby"] == "start/dateTime"
    assert call.headers["Prefer"] == 'outlook.timezone="Asia/Singapore"'
    assert [e.id for e in events] == [EVENT_ID]


def test_list_events_rejects_inverted_range(graph: FakeGraphClient) -> None:
    service = CalendarService(graph, SGT)  # type: ignore[arg-type]
    now = datetime.now(SGT)
    with pytest.raises(ValueError):
        service.list_events(now, now)


def test_find_conflicts_excludes_self_and_cancelled(graph: FakeGraphClient) -> None:
    graph.calendar_view = [
        graph_event(),
        graph_event(event_id="other", subject="Other", start="2026-10-07T14:30:00"),
        graph_event(event_id="cancelled", subject="Gone", isCancelled=True),
    ]
    service = CalendarService(graph, SGT)  # type: ignore[arg-type]
    start = datetime(2026, 10, 7, 14, 0, tzinfo=SGT)
    conflicts = service.find_conflicts(start, start + timedelta(hours=1), exclude_id=EVENT_ID)
    assert [c.id for c in conflicts] == ["other"]


def test_search_events_is_case_insensitive(graph: FakeGraphClient) -> None:
    service = CalendarService(graph, SGT)  # type: ignore[arg-type]
    start = datetime(2026, 10, 7, tzinfo=SGT)
    assert service.search_events(start, start + timedelta(days=1), "PROJECT")
    assert not service.search_events(start, start + timedelta(days=1), "stand-up")


def test_create_event_posts_exact_payload(graph: FakeGraphClient) -> None:
    service = CalendarService(graph, SGT)  # type: ignore[arg-type]
    start = datetime(2026, 10, 7, 14, 0, tzinfo=SGT)
    draft = EventDraft(subject="Review", start=start, end=start + timedelta(minutes=45))
    created = service.create_event(draft)
    call = graph.calls[-1]
    assert call.method == "POST" and call.path == "/me/events"
    assert call.json == draft.to_graph_payload(SGT)
    assert created.id == "NEW-ID"


def test_event_path_is_url_quoted(graph: FakeGraphClient) -> None:
    service = CalendarService(graph, SGT)  # type: ignore[arg-type]
    graph.events_by_id["a/b=c"] = graph_event(event_id="a/b=c")
    service.get_event("a/b=c")
    assert graph.calls[-1].path == "/me/events/a%2Fb%3Dc"


def test_get_all_follows_next_link() -> None:
    """GraphClient pagination: nextLink is followed without re-sending params."""
    from outlook_calendar_agent.graph_client import GraphClient

    class Response:
        def __init__(self, body):
            self.status_code = 200
            self.content = b"x"
            self._body = body
            self.headers = {}

        def json(self):
            return self._body

    class Session:
        def __init__(self):
            self.requests = []

        def request(self, method, url, params=None, json=None, headers=None, timeout=None):
            self.requests.append((url, params, headers))
            if url.endswith("/me/calendarView"):
                return Response({"value": [1], "@odata.nextLink": "https://g/next?$skip=1"})
            return Response({"value": [2]})

    session = Session()
    client = GraphClient(lambda: "tok", session=session)  # type: ignore[arg-type]
    items = client.get_all("/me/calendarView", params={"$top": "1"}, headers={"Prefer": "x"})
    assert items == [1, 2]
    assert session.requests[0][1] == {"$top": "1"}
    assert session.requests[1][0] == "https://g/next?$skip=1" and session.requests[1][1] is None
    assert session.requests[1][2]["Prefer"] == "x"
    assert session.requests[1][2]["Authorization"] == "Bearer tok"
