"""Master event log (master/events.jsonl) and the terminal digest.

Each event is one durable line and one printed digest line, which is how the
user is kept updated in the terminal; the dashboard reads the same file.
"""

from __future__ import annotations

from collections.abc import Mapping

from autoexp.core.models import JsonValue, MasterEvent, MasterEventKind
from autoexp.state.layout import StateLayout


class MasterEventLog:
    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout

    def emit(
        self, kind: MasterEventKind, summary: str, data: Mapping[str, JsonValue] | None = None
    ) -> MasterEvent:
        raise NotImplementedError

    def all(self) -> tuple[MasterEvent, ...]:
        raise NotImplementedError
