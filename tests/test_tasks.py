"""Google Tasks (read-only): due-date parsing, bounded list parameters and the CLI command."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pytest
from typer.testing import CliRunner

from outlook_calendar_agent import runtime as runtime_module
from outlook_calendar_agent.cli import app
from outlook_calendar_agent.google_tasks import GoogleTasksService, parse_task_due
from outlook_calendar_agent.models import TaskList
from outlook_calendar_agent.timeutil import SGT

from .conftest import Call, make_runtime

runner = CliRunner()


@dataclass
class FakeTasksClient:
    calls: list[Call] = field(default_factory=list)
    lists: list[dict[str, Any]] = field(
        default_factory=lambda: [{"id": "L1", "title": "My Tasks"}, {"id": "L2", "title": "Work"}]
    )
    tasks: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {
            "L1": [
                {
                    "id": "t1",
                    "title": "check settlement",
                    "due": "2026-10-05T00:00:00.000Z",
                    "status": "needsAction",
                },
                {
                    "id": "t2",
                    "title": "renew passport",
                    "due": "2026-10-08T00:00:00.000Z",
                    "status": "completed",
                },
            ],
            "L2": [
                {
                    "id": "t3",
                    "title": "send invoice",
                    "due": "2026-10-05T00:00:00.000Z",
                    "status": "needsAction",
                },
            ],
        }
    )

    def get_all(self, path: str, *, params=None, headers=None) -> list[dict[str, Any]]:
        self.calls.append(Call("GET", path, params=dict(params or {})))
        if path == "/users/@me/lists":
            return list(self.lists)
        list_id = path.removeprefix("/lists/").removesuffix("/tasks")
        items = self.tasks[list_id]
        if params and params.get("showCompleted") == "false":
            items = [t for t in items if t.get("status") != "completed"]
        return list(items)


def test_parse_task_due_uses_utc_calendar_date() -> None:
    assert parse_task_due("2026-10-05T00:00:00.000Z") == date(2026, 10, 5)
    assert parse_task_due(None) is None


def test_list_tasks_bounds_and_sorting() -> None:
    client = FakeTasksClient()
    service = GoogleTasksService(client, SGT)  # type: ignore[arg-type]
    start, end = datetime(2026, 10, 5, tzinfo=SGT), datetime(2026, 10, 12, tzinfo=SGT)
    tasks = service.list_all_tasks(start, end)
    assert [t.title for t in tasks] == ["check settlement", "send invoice"]
    assert tasks[0].due == date(2026, 10, 5) and tasks[1].list_name == "Work"
    call = client.calls[-1]
    assert call.params["dueMin"] == "2026-10-05T00:00:00Z"
    assert call.params["dueMax"] == "2026-10-12T00:00:00Z"
    assert call.params["showCompleted"] == "false"
    everything = service.list_all_tasks(start, end, include_completed=True)
    assert [t.title for t in everything] == ["check settlement", "send invoice", "renew passport"]
    with pytest.raises(ValueError):
        service.list_tasks(start, start, task_list=TaskList(id="L1", name="x"))


def test_tasks_command(tmp_path, google, monkeypatch) -> None:
    rt = make_runtime(tmp_path, google, provider="google")
    rt.tasks = GoogleTasksService(FakeTasksClient(), SGT)  # type: ignore[arg-type]
    monkeypatch.setattr(runtime_module, "get_runtime", lambda: rt)
    result = runner.invoke(app, ["tasks", "--from", "2026-10-05", "--to", "2026-10-12"])
    assert result.exit_code == 0, result.output
    assert "Tasks due 2026-10-05 to 2026-10-12" in result.output
    assert "2026-10-05\n  [ ] check settlement\n  [ ] send invoice | Work" in result.output
    assert "renew passport" not in result.output
    done = runner.invoke(
        app, ["tasks", "--from", "2026-10-05", "--to", "2026-10-12", "--include-completed"]
    )
    assert "[x] renew passport" in done.output


def test_tasks_command_without_tasks_support(tmp_path, google, monkeypatch) -> None:
    rt = make_runtime(tmp_path, google, provider="google")
    monkeypatch.setattr(runtime_module, "get_runtime", lambda: rt)
    result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 1 and "tasks.readonly" in result.output
