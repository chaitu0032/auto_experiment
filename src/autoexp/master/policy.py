"""Pure helpers the loop uses to read the experiment's state.

Everything here is a function of on-disk records, so a restarted master
reaches the same conclusions.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from autoexp.core.ids import RunId
from autoexp.core.models import (
    ApprovalKind,
    ApprovalRequest,
    ApprovalStatus,
    CurveSummary,
    MasterEvent,
    MasterEventKind,
    RunPurpose,
    RunRecord,
    WorkerEvent,
)


def current_round(runs: Iterable[RunRecord]) -> int:
    return max((r.spec.round_index for r in runs), default=0)


def live_runs(runs: Iterable[RunRecord]) -> tuple[RunRecord, ...]:
    return tuple(r for r in runs if not r.status.is_terminal)


def round_settled(runs: tuple[RunRecord, ...], round_index: int) -> bool:
    return all(r.status.is_terminal for r in runs if r.spec.round_index == round_index)


def _best(record: RunRecord, summaries: Mapping[RunId, CurveSummary]) -> float:
    s = summaries.get(record.spec.run_id)
    return s.best_rolling_return if s and s.best_rolling_return is not None else -math.inf


def rounds_without_improvement(
    runs: tuple[RunRecord, ...], summaries: Mapping[RunId, CurveSummary], min_improvement: float
) -> int:
    """Consecutive settled exploration rounds (most recent last) whose best run
    did not beat every earlier round's best by `min_improvement`. Seed-check
    rounds neither count nor reset the streak."""
    best_so_far = -math.inf
    streak = 0
    for index in sorted({r.spec.round_index for r in runs}):
        members = [r for r in runs if r.spec.round_index == index]
        if any(not r.status.is_terminal for r in members):
            break
        exploring = [r for r in members if r.spec.purpose is not RunPurpose.SEED_CHECK]
        if not exploring:
            continue
        best = max(_best(r, summaries) for r in exploring)
        if best_so_far == -math.inf or best > best_so_far + min_improvement:
            best_so_far = max(best_so_far, best)
            streak = 0
        else:
            streak += 1
    return streak


def compute_usage(
    runs: Iterable[RunRecord], summaries: Mapping[RunId, CurveSummary], now: float
) -> tuple[float, int]:
    """(device-seconds used by training processes, environment steps taken)."""
    seconds = 0.0
    steps = 0
    for r in runs:
        if r.started_at is not None:
            seconds += max(0.0, (r.ended_at or now) - r.started_at)
        s = summaries.get(r.spec.run_id)
        if s is not None:
            steps += s.step
    return seconds, steps


def llm_usage(events: Iterable[MasterEvent]) -> tuple[int, float]:
    calls, cost = 0, 0.0
    for e in events:
        if e.kind is MasterEventKind.LLM_CALL:
            calls += 1
            value = e.data.get("cost_usd")
            cost += float(value) if isinstance(value, int | float) else 0.0
    return calls, cost


def budget_extensions(approvals: Iterable[ApprovalRequest]) -> dict[str, float]:
    extra: dict[str, float] = {}
    for a in approvals:
        if a.kind is ApprovalKind.BUDGET_EXTENSION and a.status is ApprovalStatus.APPROVED:
            name, amount = a.payload.get("budget"), a.payload.get("extra")
            if isinstance(name, str) and isinstance(amount, int | float):
                extra[name] = extra.get(name, 0.0) + float(amount)
    return extra


def crash_verdicts(events: Iterable[WorkerEvent]) -> dict[RunId, str]:
    """Latest verdict per run from worker reports ('retry' or 'unfixable')."""
    verdicts: dict[RunId, str] = {}
    for e in events:
        if e.run_id and e.verdict in ("retry", "unfixable"):
            verdicts[e.run_id] = e.verdict
    return verdicts
