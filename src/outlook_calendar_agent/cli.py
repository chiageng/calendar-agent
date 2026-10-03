"""Typer CLI: sign-in and read-only commands. Write commands live in ``write_commands``."""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Any

import typer

from . import __version__, runtime
from .errors import AgentError
from .formatting import format_calendars, format_event_list, format_signed_in
from .timeutil import DATETIME_HELP, format_dt, parse_user_datetime, start_of_day

# pretty_exceptions_show_locals=False matters: a rich traceback could otherwise dump
# local variables, which may include an access token.
app = typer.Typer(
    help="Safe CLI for managing your Google Calendar (or Outlook via Microsoft Graph).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
    pretty_exceptions_enable=False,
)


def echo(message: str = "") -> None:
    typer.echo(message)


def echo_err(message: str) -> None:
    typer.echo(message, err=True)


def handle_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    """Convert AgentError into a clean message plus the error's exit code."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except AgentError as exc:
            echo_err(exc.render())
            raise typer.Exit(code=exc.exit_code) from None
        except KeyboardInterrupt:
            echo_err("\nInterrupted.")
            raise typer.Exit(code=130) from None

    return wrapper


@app.callback()
def _root(
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
) -> None:
    if version:
        echo(f"outlook-calendar-agent {__version__}")
        raise typer.Exit()


@app.command()
@handle_errors
def login(
    manual: Annotated[
        bool,
        typer.Option(
            "--manual",
            help="Google only: do not open a local browser; paste the redirected URL instead "
            "(use over SSH / headless).",
        ),
    ] = False,
    no_wait: Annotated[
        bool,
        typer.Option(
            "--no-wait",
            help="With --manual: print the sign-in URL and exit; finish later with "
            "'login-complete <redirected URL>'.",
        ),
    ] = False,
) -> None:
    """Sign in to the configured provider and cache tokens locally (mode 600)."""
    rt = runtime.get_runtime()
    settings = rt.settings
    echo(f"Provider: {settings.provider}    Scopes: {' '.join(settings.scopes)}")
    if not settings.can_write:
        echo("Mode: read-only (write scope not requested)")
    if settings.provider == "google":
        if no_wait and not manual:
            raise AgentError("--no-wait requires --manual.")
        claims = rt.auth.login(echo, manual=manual, wait=not no_wait)
        if no_wait:
            return
        who = claims.get("email") or "(unknown account)"
    else:
        if manual or no_wait:
            raise AgentError("--manual/--no-wait apply to the Google provider only.")
        claims = rt.auth.login(echo)
        who = claims.get("name") or claims.get("preferred_username") or "(unknown)"
    echo(f"Signed in as {who}. Token file: {settings.token_cache_path}")


@app.command(name="login-complete")
@handle_errors
def login_complete(
    redirected_url: Annotated[
        str, typer.Argument(help="The full http://127.0.0.1:... URL from the browser address bar.")
    ],
) -> None:
    """Finish a Google sign-in started with 'login --manual --no-wait'."""
    rt = runtime.get_runtime()
    if rt.settings.provider != "google":
        raise AgentError("login-complete applies to the Google provider only.")
    claims = rt.auth.complete_login(redirected_url)  # type: ignore[attr-defined]
    who = claims.get("email") or "(unknown account)"
    echo(f"Signed in as {who}. Token file: {rt.settings.token_cache_path}")


@app.command()
@handle_errors
def logout() -> None:
    """Revoke/remove cached credentials and delete the local token file."""
    rt = runtime.get_runtime()
    removed = rt.auth.logout()
    echo(f"Removed {removed} cached account(s). Token file deleted.")


@app.command()
@handle_errors
def whoami() -> None:
    """Authenticated smoke test: show the signed-in account."""
    rt = runtime.get_runtime()
    echo(format_signed_in(rt.calendar.get_me()))


@app.command()
@handle_errors
def events(
    days: Annotated[
        int, typer.Option("--days", min=1, max=90, help="Show today plus the next N-1 days.")
    ] = 7,
    from_: Annotated[
        str | None, typer.Option("--from", help=f"Range start. {DATETIME_HELP}")
    ] = None,
    to: Annotated[
        str | None, typer.Option("--to", help="Range end (exclusive). Defaults to --from + --days.")
    ] = None,
    calendar: Annotated[
        str | None,
        typer.Option("--calendar", help="Calendar name or ID (default: primary). See 'calendars'."),
    ] = None,
) -> None:
    """List events in a bounded time window of one calendar (default: primary)."""
    from .write_flow import resolve_calendar

    rt = runtime.get_runtime()
    tz = rt.tz
    calendar_id = resolve_calendar(rt, calendar)
    try:
        if from_:
            start = parse_user_datetime(from_, tz=tz)
            end = parse_user_datetime(to, tz=tz) if to else start + timedelta(days=days)
        else:
            if to:
                raise AgentError("--to requires --from.")
            start = start_of_day(datetime.now(tz), tz)
            end = start + timedelta(days=days)
    except ValueError as exc:
        raise AgentError(str(exc)) from exc
    if end <= start:
        raise AgentError("--to must be after --from.")
    if end - start > timedelta(days=90):
        raise AgentError("The window may not exceed 90 days; narrow the range.")
    events_found = rt.calendar.list_events(start, end, calendar_id=calendar_id)
    echo(format_event_list(events_found, start, end, tz, calendar_id=calendar_id))


@app.command()
@handle_errors
def calendars() -> None:
    """List the calendars this account can see, with the IDs usable in --calendar."""
    rt = runtime.get_runtime()
    echo(format_calendars(rt.calendar.list_calendars()))


@app.command()
@handle_errors
def drafts() -> None:
    """List saved drafts (proposals that have not been executed)."""
    rt = runtime.get_runtime()
    saved = rt.drafts.list()
    if not saved:
        echo("No saved drafts.")
        return
    for draft in saved:
        payload = draft.payload
        if payload.kind == "create":
            detail = f"{payload.subject}  {format_dt(payload.start, rt.tz)}"
        elif payload.kind == "update":
            detail = f"{payload.original.subject}  {format_dt(payload.effective_start, rt.tz)}"
        else:
            detail = f"{payload.original.subject}  {format_dt(payload.original.start, rt.tz)}"
        echo(f"{draft.id}  {draft.kind:<6}  {detail}")


def main() -> None:
    try:
        app()
    except AgentError as exc:  # safety net for errors outside a command body
        echo_err(exc.render())
        sys.exit(exc.exit_code)


from . import write_commands  # noqa: E402  (registers write commands on `app`)

write_commands.register(app)
