"""DecisionEngine interface and its input."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from autoexp.config.schema import GoalConfig, ParamSpace
from autoexp.core.ids import RunId
from autoexp.core.models import BudgetSnapshot, CurveSummary, Decision, PruneVerdict, RunRecord


@dataclass(frozen=True, slots=True)
class DecisionContext:
    round_index: int
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
