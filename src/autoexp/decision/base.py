"""DecisionEngine interface, its input, and the shared experiment analysis.

Division of labour: the verification protocol is deterministic and always
applies (a config that solved the goal is re-run on fresh seeds; one that holds
on `seeds_required` seeds ends the experiment). Engines only choose what to
explore next and may write the final narrative.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from autoexp.config.schema import GoalConfig, ParamSpace
from autoexp.core.ids import RunId
from autoexp.core.models import (
    BudgetSnapshot,
    CurveSummary,
    Decision,
    ParamValue,
    PruneVerdict,
    RunPurpose,
    RunRecord,
    RunStatus,
)


@dataclass(frozen=True, slots=True)
class DecisionContext:
    round_index: int  # the round the decision will start
    free_slots: int
    goal: GoalConfig
    search_space: Mapping[str, ParamSpace]
    runs: tuple[RunRecord, ...]
    summaries: Mapping[RunId, CurveSummary]
    prunes: tuple[PruneVerdict, ...]
    budget: BudgetSnapshot
    rounds_without_improvement: int


@runtime_checkable
class DecisionEngine(Protocol):
    name: str

    def decide(self, context: DecisionContext) -> Decision: ...

    def narrate(self, context: DecisionContext, stop_reason: str) -> str | None:
        """Optional prose for the final finding (None = template only)."""
        ...


def config_key(config: Mapping[str, ParamValue]) -> str:
    return json.dumps(dict(config), sort_keys=True, separators=(",", ":"))


def score(record: RunRecord, summaries: Mapping[RunId, CurveSummary]) -> float:
    summary = summaries.get(record.spec.run_id)
    if summary is None or summary.best_rolling_return is None:
        return -math.inf
    return summary.best_rolling_return


def is_solved(record: RunRecord, summaries: Mapping[RunId, CurveSummary], goal: GoalConfig) -> bool:
    return record.status is RunStatus.SOLVED or score(record, summaries) >= goal.threshold


@dataclass(frozen=True, slots=True)
class ConfigStanding:
    key: str
    config: Mapping[str, ParamValue]
    run_ids: tuple[RunId, ...]
    solved_seeds: frozenset[int]
    failed_seeds: frozenset[int]  # terminal runs that did not reach the goal
    live: int
    best: float
    first_solver: RunId | None

    @property
    def refuted(self) -> bool:
        return bool(self.solved_seeds) and bool(self.failed_seeds)

    def holds(self, required: int) -> bool:
        return len(self.solved_seeds) >= required and not self.refuted


def standings(context: DecisionContext) -> tuple[ConfigStanding, ...]:
    """Per-configuration view across seeds, best first."""
    groups: dict[str, list[RunRecord]] = {}
    for record in context.runs:
        if record.spec.purpose is RunPurpose.RETRY and record.status is RunStatus.CRASHED:
            continue
        groups.setdefault(config_key(record.spec.config), []).append(record)
    result = []
    for key, records in groups.items():
        solved = [r for r in records if is_solved(r, context.summaries, context.goal)]
        failed = [
            r
            for r in records
            if r.status.is_terminal
            and r.status is not RunStatus.LOST
            and not is_solved(r, context.summaries, context.goal)
        ]
        result.append(
            ConfigStanding(
                key=key,
                config=records[0].spec.config,
                run_ids=tuple(r.spec.run_id for r in records),
                solved_seeds=frozenset(r.spec.seed for r in solved),
                failed_seeds=frozenset(r.spec.seed for r in failed),
                live=sum(1 for r in records if not r.status.is_terminal),
                best=max(score(r, context.summaries) for r in records),
                first_solver=solved[0].spec.run_id if solved else None,
            )
        )
    return tuple(sorted(result, key=lambda s: s.best, reverse=True))


def winner(context: DecisionContext) -> ConfigStanding | None:
    for standing in standings(context):
        if standing.holds(context.goal.seeds_required):
            return standing
    return None
