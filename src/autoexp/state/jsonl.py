"""Append-only JSONL files and atomic JSON documents.

- `append_line` writes one JSON object plus newline with a single O_APPEND
  write and fsync, so concurrent appenders never interleave within a line.
- `read_lines` tolerates a partial last line (a writer mid-append) and
  malformed lines, returning them as counted errors instead of raising.
- `write_json_atomic` writes a temp file in the same directory and renames it.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ReadResult:
    records: tuple[Mapping[str, Any], ...]
    next_offset: int  # byte offset after the last complete line
    malformed: int


def _dumps(record: Mapping[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), sort_keys=True, default=_default)


def _default(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def append_line(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (_dumps(record) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_lines(path: Path, offset: int = 0) -> ReadResult:
    """Read complete lines from `offset`. Missing file -> empty result."""
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return ReadResult(records=(), next_offset=offset, malformed=0)
    end = data.rfind(b"\n")
    if end < 0:
        return ReadResult(records=(), next_offset=offset, malformed=0)
    complete = data[: end + 1]
    records: list[Mapping[str, Any]] = []
    malformed = 0
    for raw in complete.splitlines():
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            malformed += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            malformed += 1
    return ReadResult(records=tuple(records), next_offset=offset + end + 1, malformed=malformed)


def write_json_atomic(path: Path, document: Mapping[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True, default=_default)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any | None:
    """Parsed JSON document, or None when the file is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
