"""Master event log (master/events.jsonl) and the terminal digest.

Each event is one durable line and one printed digest line, which is how the
user is kept updated in the terminal; the dashboard reads the same file.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping

from autoexp.core.models import JsonValue, MasterEvent, MasterEventKind
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import append_line, read_lines
from autoexp.state.layout import StateLayout


def _print_digest(event: MasterEvent) -> None:
    stamp = time.strftime("%H:%M:%S", time.localtime(event.at))
    sys.stdout.write(f"[{stamp}] {event.kind.value:<18} {event.summary}\n")
    sys.stdout.flush()


class MasterEventLog:
    def __init__(
        self, layout: StateLayout, printer: Callable[[MasterEvent], None] | None = _print_digest
    ) -> None:
        self._layout = layout
        self._printer = printer

    def emit(
        self, kind: MasterEventKind, summary: str, data: Mapping[str, JsonValue] | None = None
    ) -> MasterEvent:
        event = MasterEvent(at=time.time(), kind=kind, summary=summary, data=dict(data or {}))
        append_line(self._layout.master_events, encode(event))
        if self._printer is not None:
            self._printer(event)
        return event

    def all(self) -> tuple[MasterEvent, ...]:
        return tuple(e for _, e in self.numbered())

    def numbered(self, after: int = 0) -> tuple[tuple[int, MasterEvent], ...]:
        """(seq, event) pairs; seq is the 1-based line number among valid events."""
        result = read_lines(self._layout.master_events)
        out = []
        seq = 0
        for raw in result.records:
            try:
                event = decode(MasterEvent, raw)
            except CodecError:
                continue
            seq += 1
            if seq > after:
                out.append((seq, event))
        return tuple(out)
