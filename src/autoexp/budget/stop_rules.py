"""Stop rules: the loop ends on the first rule that fires.

Rules: goal_reached, wall_clock, compute, llm_budget, no_improvement,
crash_limit, max_rounds, user_stop. Each is a small pure object over a
StopContext so it is unit-testable without a running master.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from autoexp.config.schema import StopConfig
from autoexp.core.models import BudgetSnapshot, StopVerdict


@dataclass(frozen=True, slots=True)
class StopContext:
    budget: BudgetSnapshot
    round_index: int
    rounds_without_improvement: int
    unfixable_crashes: int
    goal_reached: bool
    user_stop: bool


class StopRule(Protocol):
    name: str

    def check(self, context: StopContext) -> StopVerdict: ...


def default_rules(config: StopConfig) -> tuple[StopRule, ...]:
    """Rules in priority order: user_stop, goal_reached, budgets, crash_limit,
    no_improvement, max_rounds."""
    raise NotImplementedError


def evaluate(rules: tuple[StopRule, ...], context: StopContext) -> StopVerdict:
    """First firing rule wins; otherwise should_stop=False."""
    raise NotImplementedError
