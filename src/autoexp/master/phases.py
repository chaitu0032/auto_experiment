"""The phases of one master tick, as functions over (deps, records, state).

Each phase reads on-disk records, writes intents/effects idempotently, and
returns a new LoopState where it tracks anything. `loop.py` sequences them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from autoexp.budget.stop_rules import StopContext
from autoexp.core.ids import DeviceId, RunId, WorkerId
from autoexp.core.models import (
    ApprovalKind,
    BudgetSnapshot,
    CurveSummary,
    Decision,
    DecisionAction,
    EventSource,
    InboxKind,
    LockKind,
    MasterEvent,
    MasterEventKind,
    Observation,
    ProposedRun,
    PruneAction,
    PruneVerdict,
    RunEvent,
    RunEventKind,
    RunPurpose,
    RunRecord,
    RunStatus,
    StopVerdict,
    WorkerRecord,
)
from autoexp.decision.base import DecisionContext, standings, winner
from autoexp.master import policy
from autoexp.master.finding import build_finding, write_finding
from autoexp.observe.logs import read_tail, tail
from autoexp.pruning.base import Cohort
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.recovery import assign_msg_id, mark_lost
from autoexp.worker.state import worker_events

if TYPE_CHECKING:
    from autoexp.master.loop import LoopState, MasterDeps

LOCK_KIND_MASTER = LockKind.MASTER
LAUNCH_TIMEOUT_S = 240.0
NUDGE_EVERY_S = 20.0
HELD_REPORT_EVERY_S = 60.0
STOP_DRAIN_S = 90.0
FORCE_DRAIN_S = 30.0
DEBUG_TIMEOUT_S = 300.0
BUDGET_WARN_FRACTION = 0.85
BUDGET_EXTENSION_FRACTION = 0.5


def _worker(workers: tuple[WorkerRecord, ...], worker_id: WorkerId | None) -> WorkerRecord | None:
    return next((w for w in workers if w.worker_id == worker_id), None)


def _send(
    d: MasterDeps,
    workers: tuple[WorkerRecord, ...],
    worker_id: WorkerId | None,
    msg_id: str,
    kind: InboxKind,
    body: dict,  # type: ignore[type-arg]
) -> None:
    worker = _worker(workers, worker_id)
    if worker is None:
        return
    result = d.steering.send(worker, msg_id, kind, body)
    if result.message is not None:
        d.events.emit(
            MasterEventKind.STEERED,
            f"{kind.value} -> {worker.worker_id}: {msg_id}"
            + (" (nudged)" if result.nudged else ""),
            {"worker_id": worker.worker_id, "msg_id": msg_id, "kind": kind.value},
        )


def finished_verdict(events: tuple[MasterEvent, ...]) -> StopVerdict | None:
    for e in reversed(events):
        if e.kind is MasterEventKind.STOPPED and e.data.get("phase") == "done":
            rule = e.data.get("rule")
            return StopVerdict(True, str(rule) if rule else None, e.summary)
    return None


# ------------------------------------------------------------------ reconcile


def reconcile(d: MasterDeps, records: tuple[RunRecord, ...], now: float) -> None:
    """Runs whose supervisor died become LOST; assigned runs that never launch too."""
    for r in records:
        rid = r.spec.run_id
        if r.status is RunStatus.RUNNING and not d.locks.probe(d.layout.run_lock(rid)):
            current = d.registry.get(rid)
            if current is not None and current.status is RunStatus.RUNNING:
                mark_lost(d.registry, rid, "run supervisor exited without a final event")
                d.events.emit(
                    MasterEventKind.RUN_FINISHED,
                    f"{rid} lost (supervisor gone)",
                    {"run_id": rid, "status": "lost"},
                )
        elif (
            r.status is RunStatus.ASSIGNED
            and r.worker_id is not None
            and r.assigned_at is not None
            and d.inbox.is_handled(r.worker_id, assign_msg_id(rid))
            and now - r.assigned_at > LAUNCH_TIMEOUT_S
            and not d.locks.probe(d.layout.run_lock(rid))
        ):
            mark_lost(d.registry, rid, "assigned but never launched")


# --------------------------------------------------------------------- ingest


def relay_worker_reports(
    d: MasterDeps, workers: tuple[WorkerRecord, ...], state: LoopState
) -> LoopState:
    relayed = dict(state.relayed)
    for worker in workers:
        events, _ = worker_events(d.layout, worker.worker_id)
        start = relayed.get(worker.worker_id, 0)
        for seq, e in enumerate(events[start:], start=start + 1):
            run = f" {e.run_id}" if e.run_id else ""
            verdict = f" [{e.verdict}]" if e.verdict else ""
            d.events.emit(
                MasterEventKind.WORKER_REPORT,
                f"{worker.worker_id} {e.state.value}{run}{verdict}: {e.note}"[:300],
                {
                    "worker_id": worker.worker_id,
                    "seq": seq,
                    "state": e.state.value,
                    "run_id": e.run_id,
                    "verdict": e.verdict,
                    "note": e.note[:500],
                },
            )
        relayed[worker.worker_id] = len(events)
    return replace(state, relayed=relayed)


def note_finished_runs(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    workers: tuple[WorkerRecord, ...],
    state: LoopState,
) -> LoopState:
    finished = set(state.seen_finished)
    for r in records:
        rid = r.spec.run_id
        if not r.status.is_terminal or rid in finished:
            continue
        s = state.cache.summaries.get(rid)
        best = (
            f", best {s.best_rolling_return:.1f}" if s and s.best_rolling_return is not None else ""
        )
        d.events.emit(
            MasterEventKind.RUN_FINISHED,
            f"{rid} {r.status.value}{best}: {r.stop_reason or ''}"[:300],
            {
                "run_id": rid,
                "status": r.status.value,
                "reason": r.stop_reason,
                "best_rolling_return": s.best_rolling_return if s else None,
            },
        )
        finished.add(rid)
        if r.status is RunStatus.CRASHED:
            log = read_tail(d.layout.run_stdout(rid), 6000)
            _send(
                d,
                workers,
                r.worker_id,
                f"{rid}-debug",
                InboxKind.DEBUG,
                {
                    "run_id": rid,
                    "reason": r.stop_reason or "",
                    "log_tail": tail(log, 30),
                    "stdout": str(d.layout.run_stdout(rid)),
                },
            )
    return replace(state, seen_finished=frozenset(finished))


def _all_worker_events(d: MasterDeps):  # type: ignore[no-untyped-def]
    out = []
    if d.layout.workers_dir.exists():
        for path in sorted(d.layout.workers_dir.iterdir()):
            if path.is_dir():
                events, _ = worker_events(d.layout, WorkerId(path.name))
                out.extend(events)
    return out


def handle_crashes_and_losses(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    workers: tuple[WorkerRecord, ...],
    summaries: Mapping[RunId, CurveSummary],
    now: float,
) -> None:
    """Retry a lost run, or a crashed run its worker judged transient, once."""
    verdicts = policy.crash_verdicts(_all_worker_events(d))
    retried = {r.spec.parent_run_id for r in records if r.spec.purpose is RunPurpose.RETRY}
    for r in records:
        rid = r.spec.run_id
        if rid in retried or r.spec.purpose is RunPurpose.RETRY:
            continue
        wants_retry = r.status is RunStatus.LOST or (
            r.status is RunStatus.CRASHED and verdicts.get(rid) == "retry"
        )
        if not wants_retry:
            continue
        proposal = ProposedRun(
            config=dict(r.spec.config),
            seed=r.spec.seed,
            purpose=RunPurpose.RETRY,
            parent_run_id=rid,
            rationale=f"retry of {rid} ({r.status.value})",
        )
        spec = d.registry.allocate(proposal, r.spec.round_index)
        d.events.emit(
            MasterEventKind.RUN_ALLOCATED,
            f"{spec.run_id} allocated: {proposal.rationale}",
            {"run_id": spec.run_id, "round_index": spec.round_index, "purpose": "retry"},
        )


def unfixable_crashes(d: MasterDeps, records: tuple[RunRecord, ...], now: float) -> int:
    verdicts = policy.crash_verdicts(_all_worker_events(d))
    count = 0
    for r in records:
        if r.status is not RunStatus.CRASHED:
            continue
        verdict = verdicts.get(r.spec.run_id)
        timed_out = (
            verdict is None and r.ended_at is not None and now - r.ended_at > DEBUG_TIMEOUT_S
        )
        if verdict == "unfixable" or r.spec.purpose is RunPurpose.RETRY or timed_out:
            count += 1
    return count


# ---------------------------------------------------------------------- prune


def prune(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    workers: tuple[WorkerRecord, ...],
    state: LoopState,
) -> LoopState:
    round_index = policy.current_round(records)
    members = tuple(r for r in records if r.spec.round_index == round_index)
    if not any(r.status is RunStatus.RUNNING for r in members):
        return state
    observations = tuple(
        Observation(
            record=r,
            summary=state.cache.summaries.get(r.spec.run_id),
            traceback=None,
            pane_excerpt=None,
        )
        for r in members
    )
    cohort = Cohort(
        round_index,
        d.config.trainer.total_steps,
        d.config.goal.threshold,
        observations,
        decided_rungs=state.seen_rungs,
        pruned_rungs=state.pruned_rungs,
    )
    result = d.pruning.run(cohort)
    seen_prunes, seen_rungs = set(state.seen_prunes), set(state.seen_rungs)
    pruned_rungs = set(state.pruned_rungs)
    for verdict in result.verdicts:
        key = f"{verdict.run_id}:{verdict.evidence.get('rung')}"
        if (
            verdict.pruner == "halving"
            and verdict.action is PruneAction.KEEP
            and key not in seen_rungs
        ):
            seen_rungs.add(key)
            _emit_verdict(d, verdict)
    for rid, verdict in result.final.items():
        if rid in seen_prunes:
            continue
        seen_prunes.add(rid)
        if verdict.action is PruneAction.PRUNE and "rung" in verdict.evidence:
            pruned_rungs.add(f"{rid}:{verdict.evidence['rung']}")
        _emit_verdict(d, verdict)
        record = next(r for r in members if r.spec.run_id == rid)
        category = "solved" if verdict.action is PruneAction.SOLVED else "pruned"
        _send(
            d,
            workers,
            record.worker_id,
            f"{rid}-stop",
            InboxKind.STOP,
            {"run_id": rid, "category": category, "reason": f"{verdict.pruner}: {verdict.reason}"},
        )
    return replace(
        state,
        seen_prunes=frozenset(seen_prunes),
        seen_rungs=frozenset(seen_rungs),
        pruned_rungs=frozenset(pruned_rungs),
    )


def _emit_verdict(d: MasterDeps, verdict: PruneVerdict) -> None:
    verb = {"keep": "kept", "prune": "pruned", "solved": "solved"}[verdict.action.value]
    d.events.emit(
        MasterEventKind.PRUNE,
        f"{verdict.run_id} {verb} by {verdict.pruner}: {verdict.reason}"[:300],
        {
            "run_id": verdict.run_id,
            "pruner": verdict.pruner,
            "action": verdict.action.value,
            "reason": verdict.reason,
            "evidence": dict(verdict.evidence),
            **({"rung": verdict.evidence["rung"]} if "rung" in verdict.evidence else {}),
        },
    )


def prune_history(events: tuple[MasterEvent, ...]) -> tuple[PruneVerdict, ...]:
    out = []
    for e in events:
        if e.kind is MasterEventKind.PRUNE and e.data.get("action") in ("prune", "solved"):
            out.append(
                PruneVerdict(
                    run_id=str(e.data.get("run_id")),
                    pruner=str(e.data.get("pruner")),
                    action=PruneAction(str(e.data.get("action"))),
                    reason=str(e.data.get("reason", "")),
                )
            )
    return tuple(out)


# --------------------------------------------------------------------- budget


def budget_snapshot(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    summaries: Mapping[RunId, CurveSummary],
    history: tuple[MasterEvent, ...],
) -> BudgetSnapshot:
    compute_s, steps = policy.compute_usage(records, summaries, d.clock.now())
    calls, cost = policy.llm_usage(history)
    extensions = policy.budget_extensions(d.approvals.all())
    return d.ledger.snapshot(compute_s, steps, calls, cost, extensions)


def request_budget_extensions(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    summaries: Mapping[RunId, CurveSummary],
    budget: BudgetSnapshot,
) -> None:
    """Ask before overrunning: near a time budget with a winner being verified."""
    verifying = any(
        r.spec.purpose is RunPurpose.SEED_CHECK and not r.status.is_terminal for r in records
    )
    if not verifying:
        return
    for name in ("wall_clock_s", "compute_s"):
        line = d.ledger.line(budget, name)
        if line.fraction < BUDGET_WARN_FRACTION:
            continue
        approval_id = f"budget-{name.replace('_', '-')}-{int(line.limit)}"
        if d.approvals.get(approval_id) is not None:
            continue
        extra = round(line.limit * BUDGET_EXTENSION_FRACTION)
        request = d.approvals.request(
            ApprovalKind.BUDGET_EXTENSION,
            f"extend {name} by {extra:g} to finish verifying the candidate winner",
            {"budget": name, "extra": extra, "used": round(line.used, 1), "limit": line.limit},
            "master",
            approval_id=approval_id,
        )
        d.events.emit(
            MasterEventKind.APPROVAL_REQUESTED,
            f"{request.approval_id}: {request.summary}",
            {"approval_id": request.approval_id, "kind": request.kind.value},
        )


# ----------------------------------------------------------------------- stop


def stop_context(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    summaries: Mapping[RunId, CurveSummary],
    budget: BudgetSnapshot,
) -> StopContext:
    round_index = policy.current_round(records)
    context = decision_context(d, records, summaries, budget, 0)
    return StopContext(
        budget=budget,
        round_index=round_index,
        rounds_without_improvement=policy.rounds_without_improvement(
            records, summaries, d.config.stop.min_improvement
        ),
        unfixable_crashes=unfixable_crashes(d, records, d.clock.now()),
        goal_reached=winner(context) is not None,
        user_stop=d.layout.stop_flag.exists(),
        round_settled=policy.round_settled(records, round_index),
    )


def initiate_stop(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    workers: tuple[WorkerRecord, ...],
    verdict: StopVerdict,
    state: LoopState,
    now: float,
) -> LoopState:
    d.events.emit(
        MasterEventKind.STOPPED,
        f"stopping ({verdict.rule}): {verdict.reason}",
        {"phase": "initiated", "rule": verdict.rule, "reason": verdict.reason},
    )
    for r in policy.live_runs(records):
        rid = r.spec.run_id
        if r.status is RunStatus.ALLOCATED:
            mark_lost(d.registry, rid, f"never started: experiment stopping ({verdict.rule})")
            continue
        _send(
            d,
            workers,
            r.worker_id,
            f"{rid}-stop",
            InboxKind.STOP,
            {"run_id": rid, "category": "stopped", "reason": f"experiment stop: {verdict.reason}"},
        )
    return replace(state, stop=verdict, stop_started_at=now)


def drain(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    workers: tuple[WorkerRecord, ...],
    state: LoopState,
    now: float,
) -> bool:
    """True when every run has ended (or a forced stop had time to land)."""
    live = policy.live_runs(records)
    if not live:
        return True
    for r in live:
        if r.status is RunStatus.ALLOCATED:
            mark_lost(d.registry, r.spec.run_id, "never started: experiment stopping")
    return state.forced_at is not None and now - state.forced_at > FORCE_DRAIN_S


def force_stop(d: MasterDeps, records: tuple[RunRecord, ...], verdict: StopVerdict) -> None:
    """Escalation after the drain timeout: write stop requests directly.

    The one documented exception to "the master only talks to workers": an
    unresponsive worker must not keep training past a stop rule.
    """
    for r in policy.live_runs(records):
        write_json_atomic(
            d.layout.run_control(r.spec.run_id),
            {
                "action": "stop",
                "category": "stopped",
                "reason": f"forced: {verdict.reason}",
                "by": "master",
                "at": d.clock.now(),
            },
        )
        d.events.emit(
            MasterEventKind.STEER_HELD,
            f"{r.spec.run_id}: worker did not stop it; forced",
            {"run_id": r.spec.run_id, "forced": True},
        )


def finalize(
    d: MasterDeps, context: DecisionContext, verdict: StopVerdict, history: tuple[MasterEvent, ...]
) -> None:
    narrative = None
    try:
        narrative = d.decision.narrate(context, verdict.reason)
    except Exception as err:  # narrative is optional; never fail the stop
        d.events.emit(MasterEventKind.DECISION, f"narrative skipped: {err}"[:200], {})
    finding = build_finding(
        context, verdict.rule or "stopped", verdict.reason, prune_history(history), narrative
    )
    write_finding(d.layout, finding)
    d.events.emit(
        MasterEventKind.FINDING,
        finding.text.splitlines()[0][:300],
        {"winner": finding.winner_run_ids and list(finding.winner_run_ids)},
    )
    d.events.emit(
        MasterEventKind.STOPPED,
        f"stopped ({verdict.rule}): {verdict.reason}",
        {"phase": "done", "rule": verdict.rule, "reason": verdict.reason},
    )


# --------------------------------------------------------------------- decide


def decision_context(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    summaries: Mapping[RunId, CurveSummary],
    budget: BudgetSnapshot,
    free_slots: int,
) -> DecisionContext:
    history = d.events.all()
    return DecisionContext(
        round_index=policy.current_round(records) + 1,
        free_slots=free_slots,
        goal=d.config.goal,
        search_space=d.config.search_space,
        runs=records,
        summaries=summaries,
        prunes=prune_history(history),
        budget=budget,
        rounds_without_improvement=policy.rounds_without_improvement(
            records, summaries, d.config.stop.min_improvement
        ),
    )


def free_devices(d: MasterDeps, busy: set[DeviceId]) -> tuple[DeviceId, ...]:
    return tuple(
        DeviceId(slot)
        for slot in d.config.devices.slots
        if slot not in busy and not d.locks.probe(d.layout.device_lock(DeviceId(slot)))
    )


def _last_decision(history: tuple[MasterEvent, ...]) -> Decision | None:
    for e in reversed(history):
        if e.kind is MasterEventKind.DECISION and "decision" in e.data:
            try:
                return decode(Decision, e.data["decision"])
            except CodecError:
                return None
    return None


def _allocate(d: MasterDeps, decision: Decision, already: int) -> None:
    for proposal in decision.proposals[already:]:
        spec = d.registry.allocate(proposal, decision.round_index)
        d.events.emit(
            MasterEventKind.RUN_ALLOCATED,
            f"{spec.run_id} allocated (round {spec.round_index}, {proposal.purpose.value}, "
            f"seed {proposal.seed}): {proposal.rationale}"[:300],
            {
                "run_id": spec.run_id,
                "round_index": spec.round_index,
                "purpose": proposal.purpose.value,
                "config": dict(proposal.config),
            },
        )


def initial_decision(d: MasterDeps) -> Decision:
    proposals = tuple(
        ProposedRun(
            config=dict(c),
            seed=1,
            purpose=RunPurpose.EXPLORE,
            rationale=f"initial configuration {i + 1} from the experiment config",
        )
        for i, c in enumerate(d.config.initial_configs)
    )
    return Decision(
        round_index=1,
        action=DecisionAction.LAUNCH,
        proposals=proposals,
        rationale="initial configurations from the experiment config",
        engine="config",
    )


def decide(
    d: MasterDeps,
    records: tuple[RunRecord, ...],
    workers: tuple[WorkerRecord, ...],
    summaries: Mapping[RunId, CurveSummary],
    budget: BudgetSnapshot,
    history: tuple[MasterEvent, ...],
    state: LoopState,
) -> LoopState:
    last = _last_decision(history)
    if last is not None and last.action in (DecisionAction.LAUNCH, DecisionAction.VERIFY_SEEDS):
        made = sum(
            1
            for r in records
            if r.spec.round_index == last.round_index and r.spec.purpose is not RunPurpose.RETRY
        )
        if made < len(last.proposals):  # the master died mid-allocation: finish it
            _allocate(d, last, made)
            return state
    round_index = policy.current_round(records)
    if not policy.round_settled(records, round_index):
        return state
    if state.waited_with_runs == len(records):
        return state
    busy = {r.device_id for r in policy.live_runs(records) if r.device_id}
    free = d.scheduler.free_slots(policy.live_runs(records), free_devices(d, busy))
    if free <= 0:
        return state
    if not records:
        decision = initial_decision(d)
    else:
        decision = d.decision.decide(decision_context(d, records, summaries, budget, free))
    _emit_decision(d, decision)
    if decision.action is DecisionAction.STOP:
        verdict = StopVerdict(True, "goal_reached", decision.rationale)
        return initiate_stop(d, records, workers, verdict, state, d.clock.now())
    if decision.action is DecisionAction.WAIT or not decision.proposals:
        return replace(state, waited_with_runs=len(records))
    d.events.emit(
        MasterEventKind.ROUND_STARTED,
        f"round {decision.round_index}: {decision.action.value} {len(decision.proposals)} run(s)",
        {"round_index": decision.round_index, "action": decision.action.value},
    )
    _allocate(d, decision, 0)
    for i, change in enumerate(decision.code_changes):
        request = d.approvals.request(
            ApprovalKind.CODE_CHANGE,
            change.summary,
            {"diff": change.diff, "round": decision.round_index},
            f"decision:{decision.engine}",
            approval_id=f"code-r{decision.round_index}-{i + 1}",
        )
        d.events.emit(
            MasterEventKind.APPROVAL_REQUESTED,
            f"{request.approval_id}: {request.summary}",
            {"approval_id": request.approval_id, "kind": request.kind.value},
        )
    return state


def _emit_decision(d: MasterDeps, decision: Decision) -> None:
    fallback = f" (fallback: {decision.fallback_reason})" if decision.fallback_reason else ""
    d.events.emit(
        MasterEventKind.DECISION,
        f"round {decision.round_index} [{decision.engine}] {decision.action.value}: "
        f"{decision.rationale}{fallback}"[:400],
        {
            "engine": decision.engine,
            "action": decision.action.value,
            "rationale": decision.rationale,
            "fallback_reason": decision.fallback_reason,
            "round_index": decision.round_index,
            "proposals": [encode(p) for p in decision.proposals],
            "decision": encode(decision),
        },
    )


# ------------------------------------------------------------------- schedule


def schedule(
    d: MasterDeps, records: tuple[RunRecord, ...], workers: tuple[WorkerRecord, ...]
) -> None:
    pending = tuple(r for r in records if r.status is RunStatus.ALLOCATED)
    live = policy.live_runs(records)
    # Re-post assign messages lost to a crash between the event and the message.
    for r in live:
        if r.status is RunStatus.ASSIGNED and r.worker_id is not None:
            if not d.inbox.exists(r.worker_id, assign_msg_id(r.spec.run_id)):
                _send(
                    d,
                    workers,
                    r.worker_id,
                    assign_msg_id(r.spec.run_id),
                    InboxKind.ASSIGN,
                    {"run_id": r.spec.run_id, "device_id": r.device_id},
                )
    if not pending:
        return
    busy = {r.device_id for r in live if r.device_id}
    for a in d.scheduler.assign(pending, workers, live, free_devices(d, busy)):
        d.registry.append(
            RunEvent(
                run_id=a.run_id,
                at=d.clock.now(),
                kind=RunEventKind.ASSIGNED,
                source=EventSource.MASTER,
                data={"worker_id": a.worker_id, "device_id": a.device_id},
            )
        )
        _send(
            d,
            workers,
            a.worker_id,
            assign_msg_id(a.run_id),
            InboxKind.ASSIGN,
            {"run_id": a.run_id, "device_id": a.device_id},
        )


# ---------------------------------------------------------------------- steer


def deliver_approval_results(d: MasterDeps, workers: tuple[WorkerRecord, ...]) -> None:
    """Answered code-change approvals go to the first worker's coding agent."""
    if not workers:
        return
    target = sorted(workers, key=lambda w: w.worker_id)[0]
    for approval in d.approvals.all():
        if approval.kind is not ApprovalKind.CODE_CHANGE or approval.status.value == "pending":
            continue
        msg_id = f"{approval.approval_id}-result"
        if d.inbox.exists(target.worker_id, msg_id):
            continue
        _send(
            d,
            workers,
            target.worker_id,
            msg_id,
            InboxKind.APPROVAL_RESULT,
            {
                "approval_id": approval.approval_id,
                "status": approval.status.value,
                "summary": approval.summary,
                "diff": approval.payload.get("diff", ""),
            },
        )


def nudge(
    d: MasterDeps, workers: tuple[WorkerRecord, ...], state: LoopState, now: float
) -> LoopState:
    last_nudge, last_held = dict(state.last_nudge), dict(state.last_held)
    for worker in workers:
        if not d.inbox.pending(worker.worker_id):
            continue
        if now - last_nudge.get(worker.worker_id, 0.0) < NUDGE_EVERY_S:
            continue
        result = d.steering.nudge_pending(worker)
        if result.nudged:
            last_nudge[worker.worker_id] = now
        elif (
            result.held_reason and now - last_held.get(worker.worker_id, 0.0) > HELD_REPORT_EVERY_S
        ):
            last_held[worker.worker_id] = now
            d.events.emit(
                MasterEventKind.STEER_HELD,
                f"holding messages for {worker.worker_id}: {result.held_reason}",
                {"worker_id": worker.worker_id, "reason": result.held_reason},
            )
    return replace(state, last_nudge=last_nudge, last_held=last_held)


def best_standing_text(context: DecisionContext) -> str:
    ranked = standings(context)
    if not ranked or not math.isfinite(ranked[0].best):
        return "no results yet"
    return f"best {ranked[0].best:.1f} ({ranked[0].run_ids[0]})"
