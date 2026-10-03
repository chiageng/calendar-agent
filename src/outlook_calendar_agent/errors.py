"""Exception hierarchy.

Every error carries a user-facing message, an optional hint and an exit code.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all errors the CLI reports to the user."""

    exit_code: int = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        text = f"Error: {self.message}"
        if self.hint:
            text += f"\nHint: {self.hint}"
        return text


class ConfigError(AgentError):
    """Missing or invalid local configuration (.env, client ID, tenant, scopes)."""

    exit_code = 2


class AuthError(AgentError):
    """Sign-in required, consent expired or token could not be acquired."""

    exit_code = 3


class PermissionDeniedError(AgentError):
    """Microsoft Graph returned 403: the delegated permission is missing or not consented."""

    exit_code = 4


class GraphApiError(AgentError):
    """Any other Microsoft Graph error response."""

    exit_code = 5

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.status = status
        self.code = code


class NotFoundError(GraphApiError):
    """Microsoft Graph returned 404 for the requested resource."""

    exit_code = 5


class NetworkError(AgentError):
    """Could not reach Microsoft identity platform or Microsoft Graph."""

    exit_code = 6


class AmbiguousEventError(AgentError):
    """More than one calendar event matched; the user must choose explicitly."""

    exit_code = 8


class UnsupportedOperationError(AgentError):
    """The operation is intentionally deferred (recurring series, organizer changes, ...)."""

    exit_code = 9


class UserCancelledError(AgentError):
    """The user did not type the exact confirmation word."""

    exit_code = 10


class StaleDraftError(AgentError):
    """The target event changed after the draft was prepared."""

    exit_code = 11
