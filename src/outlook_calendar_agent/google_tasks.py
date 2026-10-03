"""Read-only Google Tasks support. Tasks are not calendar events and need the Tasks API."""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from urllib.parse import quote

from .google_client import GoogleClient
from .models import TaskItem, TaskList

GOOGLE_TASKS_BASE_URL = "https://tasks.googleapis.com/tasks/v1"
LIST_FIELDS = "nextPageToken,items(id,title)"
TASK_FIELDS = "nextPageToken,items(id,title,due,status,notes,completed,parent)"
PAGE_SIZE = 100


def parse_task_due(value: str | None) -> date | None:
    """Google stores a task's due date as midnight UTC; only the calendar date is meaningful."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).date()


class GoogleTasksService:
    def __init__(self, client: GoogleClient, tz: tzinfo) -> None:
        self._client = client
        self._tz = tz

    def list_task_lists(self) -> list[TaskList]:
        raw = self._client.get_all(
            "/users/@me/lists", params={"fields": LIST_FIELDS, "maxResults": str(PAGE_SIZE)}
        )
        return [TaskList(id=item["id"], name=item.get("title") or item["id"]) for item in raw]

    def list_tasks(
        self,
        start: datetime,
        end: datetime,
        *,
        task_list: TaskList,
        include_completed: bool = False,
    ) -> list[TaskItem]:
        """Tasks in one list whose due date falls within [start, end) (local dates)."""
        if end <= start:
            raise ValueError("end must be after start")
        start_day = start.astimezone(self._tz).date()
        end_day = end.astimezone(self._tz).date()
        params = {
            "dueMin": f"{start_day.isoformat()}T00:00:00Z",
            "dueMax": f"{end_day.isoformat()}T00:00:00Z",
            "showCompleted": "true" if include_completed else "false",
            "showHidden": "true" if include_completed else "false",
            "maxResults": str(PAGE_SIZE),
            "fields": TASK_FIELDS,
        }
        raw = self._client.get_all(f"/lists/{quote(task_list.id, safe='')}/tasks", params=params)
        tasks = [
            TaskItem(
                id=item["id"],
                title=item.get("title") or "(untitled task)",
                due=parse_task_due(item.get("due")),
                completed=item.get("status") == "completed",
                notes=item.get("notes") or None,
                list_name=task_list.name,
            )
            for item in raw
        ]
        tasks.sort(key=lambda t: (t.due or date.max, t.title.lower()))
        return tasks

    def list_all_tasks(
        self, start: datetime, end: datetime, *, include_completed: bool = False
    ) -> list[TaskItem]:
        tasks: list[TaskItem] = []
        for task_list in self.list_task_lists():
            tasks.extend(
                self.list_tasks(
                    start, end, task_list=task_list, include_completed=include_completed
                )
            )
        tasks.sort(key=lambda t: (t.due or date.max, t.title.lower()))
        return tasks
