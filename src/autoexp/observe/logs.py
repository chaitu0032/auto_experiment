"""Crash evidence from logs and pane text."""

from __future__ import annotations

from pathlib import Path

_TRACEBACK_HEAD = "Traceback (most recent call last):"


def find_traceback(text: str) -> str | None:
    """Return the last Python traceback block in `text`, or None."""
    start = text.rfind(_TRACEBACK_HEAD)
    if start < 0:
        return None
    block = text[start:].splitlines()
    end = len(block)
    for i, line in enumerate(block[1:], start=1):
        if line and not line.startswith((" ", "\t")):
            end = i + 1  # the exception line closes the block
            break
    return "\n".join(block[:end])


def tail(text: str, lines: int) -> str:
    """Return the last `lines` lines of `text`."""
    if lines <= 0:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def last_line(text: str | None) -> str:
    if not text:
        return ""
    stripped = [line for line in text.splitlines() if line.strip()]
    return stripped[-1].strip() if stripped else ""


def read_tail(path: Path, max_bytes: int = 16384) -> str:
    """The end of a possibly large log file, decoded leniently."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
