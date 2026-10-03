"""Explicit confirmation gate. Only the single word ``yes`` (any case) authorises a mutation."""

from __future__ import annotations

from collections.abc import Callable

CONFIRMATION_WORD = "yes"


def is_exact_confirmation(response: str | None) -> bool:
    """True only for the single word ``yes``.

    Case-insensitive and tolerant of surrounding whitespace and a trailing full stop or
    exclamation mark, because phone keyboards capitalise the first letter. Everything else
    ("y", "ok", "yes please", "yes but ...") is rejected.
    """
    if response is None:
        return False
    return response.strip().rstrip(".!").strip().lower() == CONFIRMATION_WORD


def ask_confirmation(prompt: str, *, reader: Callable[[str], str] = input) -> bool:
    """Prompt on the terminal and return True only for an exact ``yes``. EOF counts as no."""
    try:
        answer = reader(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return is_exact_confirmation(answer)
