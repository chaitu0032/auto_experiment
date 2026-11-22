"""MasterLoop: the deterministic control loop.

`run()` takes the master lock, runs recovery, opens worker panes, then calls
`tick()` every `master.tick_s` seconds until a stop rule fires and every run
has drained. Each tick runs the phases of docs/architecture.md 3.1 in order.
Every side effect is written first as an intent with a deterministic id (run
events, inbox message ids such as `r0003-assign`) and executed idempotently,
so killing the master at any point and restarting it is safe. All loop state
is either on disk or rebuilt from disk at start (`LoopState.from_events`).
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from autoexp.budget.ledger import BudgetLedger, Clock
from autoexp.budget.stop_rules import StopRule, evaluate
from autoexp.config.schema import ExperimentConfig
from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import (
    BudgetSnapshot,
    CurveSummary,
    MasterEvent,
    MasterEventKind,
    RunRecord,
    StopVerdict,
)
from autoexp.decision.base import DecisionContext, DecisionEngine
from autoexp.master import phases
from autoexp.master.events import MasterEventLog
from autoexp.master.panes import WorkerPanes
from autoexp.master.scheduler import Scheduler
from autoexp.master.steering import Steering
from autoexp.observe.observer import Observer, TelemetryCache
from autoexp.pruning.pipeline import PruningPipeline
from autoexp.state.approvals import ApprovalStore
from autoexp.state.codec import encode
from autoexp.state.inbox import Inbox
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout
from autoexp.state.locks import LockManager
from autoexp.state.recovery import recover
from autoexp.state.registry import FileRunRegistry
from autoexp.worker.state import list_workers

PANE_READ_EVERY_S = 5.0


@dataclass(frozen=True, slots=True)
class LoopState:
    """What one tick hands to the next; rebuildable from disk."""

    cache: TelemetryCache = field(default_factory=TelemetryCache)
    seen_prunes: frozenset[RunId] = frozenset()
    seen_rungs: frozenset[str] = frozenset()
    seen_finished: frozenset[RunId] = frozenset()
    relayed: Mapping[WorkerId, int] = field(default_factory=dict)
    stop: StopVerdict | None = None
    stop_started_at: float | None = None
    forced_at: float | None = None
    last_pane_read: float = 0.0
    last_nudge: Mapping[WorkerId, float] = field(default_factory=dict)
    last_held: Mapping[WorkerId, float] = field(default_factory=dict)
    pane_tails: Mapping[WorkerId, str] = field(default_factory=dict)
    waited_with_runs: int = -1  # run count when the engine last said WAIT
    tick: int = 0

    @staticmethod
    def from_events(events: tuple[MasterEvent, ...]) -> LoopState:
        prunes, rungs, finished = set(), set(), set()
        relayed: dict[WorkerId, int] = {}
        stop: StopVerdict | None = None
        stop_at: float | None = None
        for e in events:
            rid = e.data.get("run_id")
            if e.kind is MasterEventKind.PRUNE and isinstance(rid, str):
                if e.data.get("action") in ("prune", "solved"):
                    prunes.add(RunId(rid))
                elif "rung" in e.data:
                    rungs.add(f"{rid}:{e.data['rung']}")
            elif e.kind is MasterEventKind.RUN_FINISHED and isinstance(rid, str):
                finished.add(RunId(rid))
            elif e.kind is MasterEventKind.WORKER_REPORT:
                worker, seq = e.data.get("worker_id"), e.data.get("seq")
                if isinstance(worker, str) and isinstance(seq, int):
                    relayed[WorkerId(worker)] = max(relayed.get(WorkerId(worker), 0), seq)
            elif e.kind is MasterEventKind.STOPPED and e.data.get("phase") == "initiated":
                rule = e.data.get("rule")
                stop = StopVerdict(True, str(rule) if rule else None, e.summary)
                stop_at = e.at
        return LoopState(
            seen_prunes=frozenset(prunes),
            seen_rungs=frozenset(rungs),
            seen_finished=frozenset(finished),
            relayed=relayed,
            stop=stop,
            stop_started_at=stop_at,
        )


@dataclass(frozen=True, slots=True)
class TickResult:
    """What one tick did; a new value per tick (the loop holds no hidden state)."""

    state: LoopState
    budget: BudgetSnapshot
    finished: bool


@dataclass(frozen=True, slots=True)
class MasterDeps:
    """Everything the loop talks to, injected so tests swap in fakes."""

    config: ExperimentConfig
    layout: StateLayout
    locks: LockManager
    registry: FileRunRegistry
    approvals: ApprovalStore
    inbox: Inbox
    observer: Observer
    pruning: PruningPipeline
    decision: DecisionEngine
    ledger: BudgetLedger
    stop_rules: tuple[StopRule, ...]
    scheduler: Scheduler
    steering: Steering
    events: MasterEventLog
    panes: WorkerPanes
    clock: Clock


class MasterAlreadyFinished(RuntimeError):
    pass


class MasterLoop:
    def __init__(self, deps: MasterDeps) -> None:
        self._d = deps

    # ------------------------------------------------------------------ run

    def run(self, max_ticks: int | None = None, sleep: bool = True) -> StopVerdict:
        """Lock, recover, open panes, tick until stopped, write the finding."""
        d = self._d
        held = d.locks.acquire(d.layout.master_lock, phases.LOCK_KIND_MASTER, "master")
        try:
            history = d.events.all()
            done = phases.finished_verdict(history)
            if done is not None:
                raise MasterAlreadyFinished(f"experiment already finished: {done.reason}")
            report = recover(d.layout, d.registry, d.locks, d.inbox)
            if history and not report.empty:
                d.events.emit(
                    MasterEventKind.RECOVERED,
                    f"recovered: adopted {list(report.adopted)}, lost {list(report.lost)}, "
                    f"replayed {list(report.replayed_intents)}",
                    {
                        "adopted": list(report.adopted),
                        "lost": list(report.lost),
                        "replayed": list(report.replayed_intents),
                    },
                )
            d.events.emit(
                MasterEventKind.STARTED,
                f"master started for {d.config.experiment_id} "
                f"({'resumed' if history else 'new experiment'})",
                {"resumed": bool(history)},
            )
            workers = d.panes.ensure()
            d.events.emit(
                MasterEventKind.STEERED,
                "worker panes ready: "
                + ", ".join(f"{w.worker_id}={w.pane_id or 'manual'}" for w in workers),
                {"workers": [w.worker_id for w in workers]},
            )
            state = LoopState.from_events(d.events.all())
            while True:
                result = self.tick(state)
                state = result.state
                if result.finished:
                    return state.stop or StopVerdict(True, None, "finished")
                if max_ticks is not None and state.tick >= max_ticks:
                    return StopVerdict(False, None, "tick limit")
                if sleep:
                    time.sleep(d.config.master.tick_s)
        finally:
            d.locks.release(held)

    # ----------------------------------------------------------------- tick

    def tick(self, state: LoopState) -> TickResult:
        """ingest -> reconcile -> observe -> prune -> budget/stop -> decide ->
        schedule -> steer -> report."""
        d = self._d
        now = d.clock.now()
        state = replace(state, tick=state.tick + 1)
        workers = list_workers(d.layout)

        # reconcile
        phases.reconcile(d, d.registry.all(), now)
        records = d.registry.all()

        # observe
        cache = d.observer.update_telemetry(records, state.cache)
        d.observer.write_summaries(cache.summaries)
        state = replace(state, cache=cache)
        if now - state.last_pane_read >= PANE_READ_EVERY_S:
            tails = d.observer.cache_panes(workers, d.steering.read_context)
            state = replace(state, pane_tails=tails, last_pane_read=now)

        # ingest worker reports and finished runs
        state = phases.relay_worker_reports(d, workers, state)
        state = phases.note_finished_runs(d, records, workers, state)
        phases.handle_crashes_and_losses(d, records, workers, cache.summaries, now)
        records = d.registry.all()

        # prune (not while stopping: the stop already ends every run)
        if state.stop is None:
            state = phases.prune(d, records, workers, state)

        # budget
        history = d.events.all()
        budget = phases.budget_snapshot(d, records, cache.summaries, history)
        write_json_atomic(d.layout.master_budget, encode(budget))
        phases.request_budget_extensions(d, records, cache.summaries, budget)

        # stop rules
        if state.stop is None:
            verdict = evaluate(
                d.stop_rules, phases.stop_context(d, records, cache.summaries, budget)
            )
            if verdict.should_stop:
                state = phases.initiate_stop(d, records, workers, verdict, state, now)

        # decide + schedule
        if state.stop is None:
            state = phases.decide(d, records, workers, cache.summaries, budget, history, state)
            records = d.registry.all()
            if state.stop is None:
                phases.schedule(d, records, list_workers(d.layout))
        else:
            finished = phases.drain(d, d.registry.all(), workers, state, now)
            if finished:
                context = self.context(d.registry.all(), cache.summaries, budget, free_slots=0)
                phases.finalize(d, context, state.stop, history)
                self._heartbeat(now, state, "finished")
                return TickResult(state=state, budget=budget, finished=True)
            if state.forced_at is None and state.stop_started_at is not None:
                if now - state.stop_started_at > phases.STOP_DRAIN_S:
                    phases.force_stop(d, d.registry.all(), state.stop)
                    state = replace(state, forced_at=now)

        # steer
        state = phases.nudge(d, list_workers(d.layout), state, now)
        self._heartbeat(now, state, "running" if state.stop is None else "stopping")
        return TickResult(state=state, budget=budget, finished=False)

    def context(
        self,
        records: tuple[RunRecord, ...],
        summaries: Mapping[RunId, CurveSummary],
        budget: BudgetSnapshot,
        free_slots: int,
    ) -> DecisionContext:
        return phases.decision_context(self._d, records, summaries, budget, free_slots)

    def _heartbeat(self, now: float, state: LoopState, status: str) -> None:
        write_json_atomic(
            self._d.layout.master_heartbeat,
            {"at": now, "tick": state.tick, "status": status, "pid": os.getpid()},
        )
