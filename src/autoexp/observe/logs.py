"""Crash evidence from logs and pane text."""

from __future__ import annotations


def find_traceback(text: str) -> str | None:
    """Return the last Python traceback block in `text`, or None."""
    raise NotImplementedError


def tail(text: str, lines: int) -> str:
    """Return the last `lines` lines of `text`."""
    raise NotImplementedError
