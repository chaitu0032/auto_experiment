"""Crash recovery: rebuild the master's view from disk before the first tick.

Steps (docs/architecture.md 5): fold all run events; probe run locks of
non-terminal runs (held -> re-adopt, free -> mark LOST); replay master intents
whose effect is missing (allocated without assign message, prune verdict
without stop message); list worker IDs owning live runs whose pane is gone.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.core.ids import RunId, WorkerId
from autoexp.state.layout import StateLayout
from autoexp.state.locks import LockManager
from autoexp.state.registry import RunRegistry


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    adopted: tuple[RunId, ...]  # live supervisors found holding their run lock
    lost: tuple[RunId, ...]  # non-terminal on disk, lock free
    replayed_intents: tuple[str, ...]  # msg ids re-posted
    workers_to_reopen: tuple[WorkerId, ...]
    round_index: int


def recover(layout: StateLayout, registry: RunRegistry, locks: LockManager) -> RecoveryReport:
    raise NotImplementedError
