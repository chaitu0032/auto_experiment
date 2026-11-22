"""Append-only JSONL files and atomic JSON documents.

- `append_line` writes one JSON object plus newline with a single O_APPEND
  write and fsync, so concurrent appenders never interleave within a line.
- `read_lines` tolerates a partial last line (a writer mid-append) and
  malformed lines, returning them as counted errors instead of raising.
- `write_json_atomic` writes a temp file in the same directory and renames it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ReadResult:
    records: tuple[Mapping[str, Any], ...]
    next_offset: int  # byte offset after the last complete line
    malformed: int


def append_line(path: Path, record: Mapping[str, Any]) -> None:
    raise NotImplementedError


def read_lines(path: Path, offset: int = 0) -> ReadResult:
    raise NotImplementedError


def write_json_atomic(path: Path, document: Mapping[str, Any]) -> None:
    raise NotImplementedError


def read_json(path: Path) -> Mapping[str, Any] | None:
    raise NotImplementedError
