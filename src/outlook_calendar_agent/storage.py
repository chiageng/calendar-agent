"""Helpers for private local state files (token cache, drafts, audit log).

Everything written here is owner-only: directories are mode 700 and files are mode 600.
"""

from __future__ import annotations

import os
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) with owner-only permissions and tighten it if it exists."""
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    os.chmod(path, DIR_MODE)
    return path


def write_private_text(path: Path, content: str) -> None:
    """Atomically replace ``path`` with ``content`` using mode 600."""
    ensure_private_dir(path.parent)
    tmp_path = path.with_name(path.name + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    os.chmod(tmp_path, FILE_MODE)
    os.replace(tmp_path, path)


def append_private_line(path: Path, line: str) -> None:
    """Append one line to ``path``, creating it with mode 600 when needed."""
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")
    os.chmod(path, FILE_MODE)
