"""Stop rules: the loop ends on the first rule that fires.

Rules: goal_reached, wall_clock, compute, env_steps, llm_budget,
no_improvement, crash_limit, max_rounds, user_stop. Each is a small pure
object over a StopContext so it is unit-testable without a running master.
The LLM budget stops the loop when `stop.stop_on_llm_budget` is true (the
default); otherwise decisions continue with the deterministic engine.
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
    round_settled: bool = True  # no live runs in the current round


class StopRule(Protocol):
    name: str

    def check(self, context: StopContext) -> StopVerdict: ...


_GO = StopVerdict(should_stop=False, rule=None, reason="")


def _stop(rule: str, reason: str) -> StopVerdict:
    return StopVerdict(should_stop=True, rule=rule, reason=reason)


def _line(context: StopContext, name: str):  # type: ignore[no-untyped-def]
    return next(line for line in context.budget.lines if line.name == name)


@dataclass(frozen=True, slots=True)
class UserStop:
    name: str = "user_stop"

    def check(self, context: StopContext) -> StopVerdict:
        return _stop(self.name, "the user asked to stop") if context.user_stop else _GO


@dataclass(frozen=True, slots=True)
class GoalReached:
    name: str = "goal_reached"

    def check(self, context: StopContext) -> StopVerdict:
        if context.goal_reached:
            return _stop(self.name, "a configuration holds the goal on the required seeds")
        return _GO


@dataclass(frozen=True, slots=True)
class BudgetExhausted:
    budget: str
    name: str = ""

    def check(self, context: StopContext) -> StopVerdict:
        line = _line(context, self.budget)
        if line.exhausted:
            return _stop(
                self.name or self.budget,
                f"{self.budget} budget spent ({line.used:.0f} of {line.limit:.0f})",
            )
        return _GO


@dataclass(frozen=True, slots=True)
class LLMBudget:
    name: str = "llm_budget"

    def check(self, context: StopContext) -> StopVerdict:
        calls, cost = _line(context, "llm_calls"), _line(context, "llm_cost_usd")
        if calls.exhausted:
            return _stop(self.name, f"LLM call budget spent ({calls.used:.0f} calls)")
        if cost.exhausted:
            return _stop(self.name, f"LLM cost budget spent (${cost.used:.2f})")
        return _GO


@dataclass(frozen=True, slots=True)
class CrashLimit:
    limit: int
    name: str = "crash_limit"

    def check(self, context: StopContext) -> StopVerdict:
        if context.unfixable_crashes >= self.limit:
            return _stop(self.name, f"{context.unfixable_crashes} unfixable crashes")
        return _GO


@dataclass(frozen=True, slots=True)
class NoImprovement:
    rounds: int
    name: str = "no_improvement"

    def check(self, context: StopContext) -> StopVerdict:
        if context.round_settled and context.rounds_without_improvement >= self.rounds:
            return _stop(
                self.name, f"{context.rounds_without_improvement} rounds without improvement"
            )
        return _GO


@dataclass(frozen=True, slots=True)
class MaxRounds:
    rounds: int
    name: str = "max_rounds"

    def check(self, context: StopContext) -> StopVerdict:
        if context.round_settled and context.round_index >= self.rounds:
            return _stop(self.name, f"reached {context.round_index} rounds")
        return _GO


def default_rules(config: StopConfig, uses_llm: bool) -> tuple[StopRule, ...]:
    """Rules in priority order: user_stop, goal_reached, budgets, crash_limit,
    no_improvement, max_rounds."""
    llm: tuple[StopRule, ...] = (LLMBudget(),) if uses_llm and config.stop_on_llm_budget else ()
    return (
        UserStop(),
        GoalReached(),
        BudgetExhausted("wall_clock_s", "wall_clock"),
        BudgetExhausted("compute_s", "compute"),
        BudgetExhausted("env_steps", "env_steps"),
        *llm,
        CrashLimit(config.crash_limit),
        NoImprovement(config.no_improvement_rounds),
        MaxRounds(config.max_rounds),
    )


def evaluate(rules: tuple[StopRule, ...], context: StopContext) -> StopVerdict:
    """First firing rule wins; otherwise should_stop=False."""
    for rule in rules:
        verdict = rule.check(context)
        if verdict.should_stop:
            return verdict
    return _GO


def llm_budget_note(context: StopContext) -> str | None:
    """Message when the LLM budget is spent (the loop continues deterministically)."""
    calls, cost = _line(context, "llm_calls"), _line(context, "llm_cost_usd")
    if calls.exhausted or cost.exhausted:
        return "LLM budget spent; decisions continue with the deterministic engine"
    return None
