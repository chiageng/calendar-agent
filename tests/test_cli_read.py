from typer.testing import CliRunner

from outlook_calendar_agent.cli import app

from .conftest import FakeGraphClient

runner = CliRunner()


def test_whoami(rt, graph: FakeGraphClient) -> None:
    result = runner.invoke(app, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "Signed in as: Chia Geng <me@example.com>" in result.output
    assert graph.calls[-1].params == {"$select": "displayName,mail,userPrincipalName"}


def test_events_with_explicit_range(rt, graph: FakeGraphClient) -> None:
    result = runner.invoke(app, ["events", "--from", "2026-10-07", "--to", "2026-10-08"])
    assert result.exit_code == 0, result.output
    assert "14:00–15:00  Project review | Room 4 | me@example.com" in result.output
    params = graph.calls[-1].params
    assert params["startDateTime"] == "2026-10-06T16:00:00Z"
    assert params["endDateTime"] == "2026-10-07T16:00:00Z"
    assert not graph.write_calls


def test_events_rejects_bad_range(rt) -> None:
    result = runner.invoke(app, ["events", "--from", "2026-10-08", "--to", "2026-10-07"])
    assert result.exit_code == 1
    assert "--to must be after --from" in result.output
    result = runner.invoke(app, ["events", "--from", "someday"])
    assert result.exit_code == 1 and "YYYY-MM-DD" in result.output


def test_login_never_prints_tokens(rt) -> None:
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0, result.output
    assert "Signed in as Chia Geng" in result.output
    assert "not-a-real-token" not in result.output


def test_drafts_empty(rt) -> None:
    result = runner.invoke(app, ["drafts"])
    assert result.exit_code == 0 and "No saved drafts" in result.output
