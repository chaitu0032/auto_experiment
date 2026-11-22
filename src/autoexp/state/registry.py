"""Run registry: allocate run IDs, write specs, append and fold run events.

The registry is the only writer of `counters.json` and `spec.json`; the master
is its only allocating caller. The current `RunRecord` of a run is always
`fold(spec, events)`; it is never stored on its own, so it cannot drift from the
event log.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from autoexp.core.ids import RunId
from autoexp.core.models import ProposedRun, RunEvent, RunRecord, RunSpec
from autoexp.state.layout import StateLayout


class RunRegistry(Protocol):
    def allocate(self, proposal: ProposedRun, round_index: int) -> RunSpec:
        """Take the next run ID under the counters lock, write spec.json and an
        `allocated` event, and return the spec."""
        ...

    def append(self, event: RunEvent) -> None: ...

    def get(self, run_id: RunId) -> RunRecord | None: ...

    def all(self) -> tuple[RunRecord, ...]: ...


def fold(spec: RunSpec, events: Iterable[RunEvent]) -> RunRecord:
    """Pure: derive the current RunRecord from a spec and its ordered events.

    Rules: `assigned` sets worker and device; `launched`/`started` move to
    RUNNING; `exited` maps exit outcome to SOLVED/COMPLETED/PRUNED/STOPPED/
    CRASHED; `lost` moves to LOST; events after a terminal status are ignored
    except for counting crashes.
    """
    raise NotImplementedError


class FileRunRegistry:
    """RunRegistry backed by `StateLayout` files."""

    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout

    def allocate(self, proposal: ProposedRun, round_index: int) -> RunSpec:
        raise NotImplementedError

    def append(self, event: RunEvent) -> None:
        raise NotImplementedError

    def get(self, run_id: RunId) -> RunRecord | None:
        raise NotImplementedError

    def all(self) -> tuple[RunRecord, ...]:
        raise NotImplementedError
