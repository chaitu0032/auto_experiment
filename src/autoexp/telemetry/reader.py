"""Master-side incremental telemetry reader.

The caller keeps a byte offset per run and gets only new complete lines each
tick; partial and malformed lines are counted, never fatal. Offsets live in the
master's memory: a restarted master rescans from offset 0, which is always
correct because summaries are a pure fold of the file.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.core.ids import RunId
from autoexp.core.models import TelemetryEvent, TelemetryKind
from autoexp.state.jsonl import read_lines
from autoexp.state.layout import StateLayout

_NON_FINITE = {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}


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
        result = read_lines(self._layout.run_metrics(run_id), offset)
        events = []
        malformed = result.malformed
        for record in result.records:
            event = parse_event(run_id, record)
            if event is None:
                malformed += 1
            else:
                events.append(event)
        return TelemetryBatch(
            run_id=run_id, events=tuple(events), next_offset=result.next_offset, malformed=malformed
        )


def _value(raw: object) -> float | int | str | None:
    if raw is None or isinstance(raw, bool):
        return None if raw is None else int(raw)
    if isinstance(raw, int | float):
        return raw
    if isinstance(raw, str):
        return _NON_FINITE.get(raw.lower(), raw)
    return None


def parse_event(run_id: RunId, record: Mapping[str, object]) -> TelemetryEvent | None:
    """Validate one decoded line; None when it is not a well-formed event."""
    try:
        kind = TelemetryKind(record.get("kind"))
    except ValueError:
        return None
    t = record.get("t")
    step = record.get("step")
    values = record.get("values")
    if isinstance(t, bool) or not isinstance(t, int | float):
        return None
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        return None
    if not isinstance(values, Mapping):
        return None
    return TelemetryEvent(
        run_id=run_id,
        t=float(t),
        step=step,
        kind=kind,
        values={str(k): _value(v) for k, v in values.items()},
    )
