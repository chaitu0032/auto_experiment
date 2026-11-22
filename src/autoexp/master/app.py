"""Build a MasterLoop from a validated config (the composition root)."""

from __future__ import annotations

import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from autoexp.budget.ledger import BudgetLedger, Clock, SystemClock
from autoexp.budget.stop_rules import default_rules
from autoexp.config.schema import EngineKind, ExperimentConfig, WorkerRuntimeKind
from autoexp.core.models import MasterEvent, MasterEventKind
from autoexp.decision.base import DecisionEngine
from autoexp.decision.deterministic import DeterministicDecisionEngine
from autoexp.decision.llm import LLMDecisionEngine
from autoexp.herdr.adapter import HerdrAdapter
from autoexp.herdr.null import NullHerdr
from autoexp.llm.client import CallRecord, ClaudeCodeClient, LLMClient, MeteredLLMClient
from autoexp.master import policy
from autoexp.master.events import MasterEventLog
from autoexp.master.loop import MasterDeps, MasterLoop
from autoexp.master.panes import WorkerPanes
from autoexp.master.scheduler import Scheduler
from autoexp.master.steering import Steering
from autoexp.observe.observer import Observer
from autoexp.pruning.base import Pruner
from autoexp.pruning.halving import SuccessiveHalvingPruner
from autoexp.pruning.llm_judge import LLMJudgePruner
from autoexp.pruning.pipeline import PruningPipeline
from autoexp.pruning.rules import DivergencePruner, PlateauPruner
from autoexp.state.approvals import FileApprovalStore
from autoexp.state.inbox import FileInbox
from autoexp.state.jsonl import read_json, write_json_atomic
from autoexp.state.layout import StateLayout
from autoexp.state.locks import FlockLockManager
from autoexp.state.registry import FileRunRegistry
from autoexp.telemetry.reader import TelemetryReader
from autoexp.worker.runtime import ClaudeCodeRuntime, ShellRuntime, WorkerRuntime


def autoexp_command(root: Path) -> str:
    """The exact command workers use to call back into autoexp."""
    return f"{shlex.quote(sys.executable)} -m autoexp --state-dir {shlex.quote(str(root))}"


def write_experiment(layout: StateLayout, config: ExperimentConfig, config_text: str) -> float:
    """Create or refresh experiment.json; returns the experiment start time."""
    existing = read_json(layout.experiment_file)
    started_at = (
        float(existing["started_at"])
        if isinstance(existing, dict) and "started_at" in existing
        else time.time()
    )
    write_json_atomic(
        layout.experiment_file,
        {
            "experiment_id": config.experiment_id,
            "started_at": started_at,
            "trainer": {
                "name": config.trainer.name,
                "command": list(config.trainer.command),
                "total_steps": config.trainer.total_steps,
            },
            "devices": {
                "slots": list(config.devices.slots),
                "threads_per_slot": config.devices.threads_per_slot,
            },
            "goal": {
                "metric": config.goal.metric,
                "window": config.goal.window,
                "threshold": config.goal.threshold,
                "seeds_required": config.goal.seeds_required,
            },
            "config_toml": config_text,
        },
    )
    return started_at


@dataclass(frozen=True, slots=True)
class BuildOptions:
    herdr: HerdrAdapter | None
    cwd: str
    engine: EngineKind | None = None  # override config.llm.engine
    runtime: WorkerRuntimeKind | None = None  # override config.workers.runtime
    llm_client: LLMClient | None = None  # tests inject a fake
    clock: Clock | None = None
    quiet: bool = False


def _llm_client(
    config: ExperimentConfig, events: MasterEventLog, inner: LLMClient | None
) -> MeteredLLMClient:
    def budget_ok() -> bool:
        calls, cost = policy.llm_usage(events.all())
        return calls < config.budget.llm_calls and cost < config.budget.llm_cost_usd

    def sink(call: CallRecord) -> None:
        status = "ok" if call.ok else f"failed: {call.error}"
        events.emit(
            MasterEventKind.LLM_CALL,
            f"LLM {call.purpose} call {status} (${call.cost_usd:.4f}, {call.duration_s:.0f}s)"[
                :300
            ],
            {
                "purpose": call.purpose,
                "ok": call.ok,
                "cost_usd": call.cost_usd,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
            },
        )

    client = inner or ClaudeCodeClient(config.llm.command, config.llm.timeout_s)
    return MeteredLLMClient(client, budget_ok, sink)


def build_master(config: ExperimentConfig, root: Path, options: BuildOptions) -> MasterLoop:
    layout = StateLayout(root)
    events = MasterEventLog(layout, None) if options.quiet else MasterEventLog(layout)
    clock = options.clock or SystemClock()
    started_at = float((read_json(layout.experiment_file) or {}).get("started_at", clock.now()))
    engine_kind = options.engine or config.llm.engine
    llm = _llm_client(config, events, options.llm_client)
    deterministic = DeterministicDecisionEngine()
    decision: DecisionEngine = (
        LLMDecisionEngine(llm, deterministic) if engine_kind is EngineKind.LLM else deterministic
    )
    pruners: list[Pruner] = []
    for name in config.pruning.order:
        if name == "divergence":
            pruners.append(DivergencePruner(config.pruning.divergence))
        elif name == "plateau":
            pruners.append(PlateauPruner(config.pruning.plateau))
        elif name == "halving":
            pruners.append(SuccessiveHalvingPruner(config.pruning.halving))
        elif name == "llm_judge" and config.pruning.llm_judge.enabled:
            pruners.append(LLMJudgePruner(config.pruning.llm_judge, llm))
    runtime_kind = options.runtime or config.workers.runtime
    command = autoexp_command(root)
    runtime: WorkerRuntime = (
        ClaudeCodeRuntime(config.workers.agent_command, command)
        if runtime_kind is WorkerRuntimeKind.CLAUDE
        else ShellRuntime(command)
    )
    herdr = options.herdr
    inbox = FileInbox(layout)
    steering = Steering(inbox, herdr or NullHerdr(), agent_runtime=runtime.uses_agent)
    deps = MasterDeps(
        config=config,
        layout=layout,
        locks=FlockLockManager(),
        registry=FileRunRegistry(
            layout, config.experiment_id, config.trainer.name, config.trainer.total_steps
        ),
        approvals=FileApprovalStore(layout),
        inbox=inbox,
        observer=Observer(
            layout, TelemetryReader(layout), config.goal.window, config.pruning.divergence.kl_max
        ),
        pruning=PruningPipeline(tuple(pruners)),
        decision=decision,
        ledger=BudgetLedger(config.budget, clock, started_at),
        stop_rules=default_rules(config.stop, uses_llm=engine_kind is EngineKind.LLM),
        scheduler=Scheduler(config.workers, config.devices),
        steering=steering,
        events=events,
        panes=WorkerPanes(
            layout,
            herdr,
            config.workers,
            runtime,
            command,
            options.cwd,
            config.herdr.workspace_label,
        ),
        clock=clock,
    )
    return MasterLoop(deps)


def last_events(layout: StateLayout, n: int) -> tuple[MasterEvent, ...]:
    return MasterEventLog(layout, None).all()[-n:]
