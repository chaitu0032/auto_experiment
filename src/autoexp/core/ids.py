"""Typed identifiers.

Run IDs are the primary key of the whole system: commands, logs, checkpoints,
telemetry and dashboard rows are all keyed by them. They are allocated by the
master only (see `state/registry.py`), in order, as `r0001`, `r0002`, ...
"""

from __future__ import annotations

import re
from typing import NewType

RunId = NewType("RunId", str)
WorkerId = NewType("WorkerId", str)
DeviceId = NewType("DeviceId", str)

RUN_ID_PATTERN = re.compile(r"^r(\d{4,})$")
WORKER_ID_PATTERN = re.compile(r"^w\d+$")
DEVICE_ID_PATTERN = re.compile(r"^(cpu|mps)\d+$")


def format_run_id(number: int) -> RunId:
    """Return the run ID for allocation counter `number` (1-based)."""
    if number < 1:
        raise ValueError(f"run number must be >= 1, got {number}")
    return RunId(f"r{number:04d}")


def parse_run_id(text: str) -> int:
    """Return the allocation counter encoded in a run ID, or raise ValueError."""
    match = RUN_ID_PATTERN.match(text)
    if match is None:
        raise ValueError(f"not a run id: {text!r}")
    return int(match.group(1))


def validate_worker_id(text: str) -> WorkerId:
    if WORKER_ID_PATTERN.match(text) is None:
        raise ValueError(f"not a worker id: {text!r}")
    return WorkerId(text)


def validate_device_id(text: str) -> DeviceId:
    if DEVICE_ID_PATTERN.match(text) is None:
        raise ValueError(f"not a device id: {text!r}")
    return DeviceId(text)
