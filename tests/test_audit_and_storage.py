import json
from datetime import datetime
from pathlib import Path

import pytest

from outlook_calendar_agent.audit import AuditLog
from outlook_calendar_agent.drafts import DraftStore
from outlook_calendar_agent.errors import AgentError
from outlook_calendar_agent.models import EventDraft
from outlook_calendar_agent.storage import write_private_text
from outlook_calendar_agent.timeutil import SGT


def _mode(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def test_audit_log_appends_json_lines_with_private_mode(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "state" / "audit.jsonl")
    log.record(
        action="create",
        stage="proposed",
        subject="Review",
        start=datetime(2026, 10, 7, 14, tzinfo=SGT),
    )
    log.record(action="create", stage="rejected", subject="Review")
    lines = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert [entry["stage"] for entry in lines] == ["proposed", "rejected"]
    assert lines[0]["start"] == "2026-10-07T14:00:00+08:00"
    assert _mode(log.path) == "0o600"
    assert _mode(log.path.parent) == "0o700"


def test_audit_log_refuses_secret_like_keys(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(ValueError, match="sensitive"):
        log.record(action="create", stage="proposed", extra={"access_token": "abc"})
    assert not log.path.exists()


def test_private_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "state" / "token_cache.json"
    write_private_text(target, "{}")
    assert _mode(target) == "0o600"
    assert not target.with_name("token_cache.json.tmp").exists()


def test_draft_store_roundtrip(tmp_path: Path) -> None:
    store = DraftStore(tmp_path / "drafts")
    start = datetime(2026, 10, 7, 14, tzinfo=SGT)
    draft = store.save(EventDraft(subject="Review", start=start, end=start.replace(hour=15)))
    assert draft.id.startswith("d-")
    assert _mode(tmp_path / "drafts" / f"{draft.id}.json") == "0o600"
    loaded = store.load(draft.id)
    assert loaded.payload.kind == "create" and loaded.payload.subject == "Review"
    assert [d.id for d in store.list()] == [draft.id]
    store.discard(draft.id)
    with pytest.raises(AgentError, match="not found"):
        store.load(draft.id)
    with pytest.raises(AgentError, match="Invalid draft ID"):
        store.load("../etc/passwd")
