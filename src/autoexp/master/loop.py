"""MasterLoop: the deterministic control loop.

`run()` takes the master lock, runs recovery, opens worker panes, then calls
`tick()` every `master.tick_s` seconds until a stop rule fires. Each tick runs
the phases of docs/architecture.md 3.1 in order. Every side effect is written
first as an intent event with a deterministic id and then executed
idempotently, so killing the master at any point and restarting it is safe.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.budget.ledger import BudgetLedger
from autoexp.budget.stop_rules import StopRule
from autoexp.config.schema import ExperimentConfig
from autoexp.core.ids import RunId
from autoexp.core.models import CurveSummary, MasterEvent, StopVerdict
from autoexp.decision.base import DecisionEngine
from autoexp.master.events import MasterEventLog
from autoexp.master.scheduler import Scheduler
from autoexp.master.steering import Steering
from autoexp.observe.observer import Observer
from autoexp.pruning.pipeline import PruningPipeline
from autoexp.state.approvals import ApprovalStore
from autoexp.state.layout import StateLayout
from autoexp.state.locks import LockManager
from autoexp.state.registry import RunRegistry


@dataclass(frozen=True, slots=True)
class TickResult:
    """What one tick did; a new value per tick (the loop holds no hidden state)."""

    events: tuple[MasterEvent, ...]
    summaries: Mapping[RunId, CurveSummary]
    stop: StopVerdict


@dataclass(frozen=True, slots=True)
class MasterDeps:
    """Everything the loop talks to, injected so tests swap in fakes."""

    config: ExperimentConfig
    layout: StateLayout
    locks: LockManager
    registry: RunRegistry
    approvals: ApprovalStore
    observer: Observer
    pruning: PruningPipeline
    decision: DecisionEngine
    ledger: BudgetLedger
    stop_rules: tuple[StopRule, ...]
    scheduler: Scheduler
    steering: Steering
    events: MasterEventLog


class MasterLoop:
    def __init__(self, deps: MasterDeps) -> None:
        self._deps = deps

    def run(self) -> StopVerdict:
        """Lock, recover, open panes, tick until stopped, write the finding."""
        raise NotImplementedError

    def tick(self, previous: Mapping[RunId, CurveSummary]) -> TickResult:
        """ingest -> reconcile -> observe -> prune -> budget/stop -> decide ->
        schedule -> steer -> report."""
        raise NotImplementedError
