"""Master-side incremental telemetry reader.

Keeps a byte offset per run (persisted in master/offsets.json as a cache) and
returns only new complete lines each tick; partial and malformed lines are
counted, never fatal. Rescanning from offset 0 is always correct.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.core.ids import RunId
from autoexp.core.models import TelemetryEvent
from autoexp.state.layout import StateLayout


@dataclass(frozen=True, slots=True)
class TelemetryBatch:
    run_id: RunId
    events: tuple[TelemetryEvent, ...]
    next_offset: int
    malformed: int


class TelemetryReader:
    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout

    def read_new(self, run_id: RunId, offset: int) -> TelemetryBatch:
        raise NotImplementedError


def parse_event(run_id: RunId, record: dict[str, object]) -> TelemetryEvent | None:
    """Validate one decoded line; None when it is not a well-formed event."""
    raise NotImplementedError
