"""Explicit confirmation gate. Only the exact word ``yes`` authorises a mutation."""

from __future__ import annotations

from collections.abc import Callable

CONFIRMATION_WORD = "yes"


def is_exact_confirmation(response: str | None) -> bool:
    """True only for ``yes`` (surrounding whitespace ignored, case-sensitive)."""
    if response is None:
        return False
    return response.strip() == CONFIRMATION_WORD


def ask_confirmation(prompt: str, *, reader: Callable[[str], str] = input) -> bool:
    """Prompt on the terminal and return True only for an exact ``yes``. EOF counts as no."""
    try:
        answer = reader(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return is_exact_confirmation(answer)
