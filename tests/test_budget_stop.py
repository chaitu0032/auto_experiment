"""Budget ledger and stop rules."""

from __future__ import annotations

from autoexp.budget.ledger import BudgetLedger
from autoexp.budget.stop_rules import StopContext, default_rules, evaluate, llm_budget_note
from autoexp.config.schema import BudgetConfig, StopConfig
from autoexp.core.models import BudgetSnapshot

CFG = BudgetConfig(wall_clock_s=100, compute_s=400, env_steps=1000, llm_calls=2, llm_cost_usd=1.0)


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def now(self) -> float:
        return self.t


def _snap(
    t: float = 50,
    compute: float = 10,
    steps: int = 10,
    calls: int = 0,
    cost: float = 0,
    ext: dict[str, float] | None = None,
) -> BudgetSnapshot:
    return BudgetLedger(CFG, _Clock(t), started_at=0).snapshot(compute, steps, calls, cost, ext)


def _ctx(budget: BudgetSnapshot, **kw: object) -> StopContext:
    base = dict(
        round_index=1,
        rounds_without_improvement=0,
        unfixable_crashes=0,
        goal_reached=False,
        user_stop=False,
        round_settled=True,
    )
    base.update(kw)
    return StopContext(budget=budget, **base)  # type: ignore[arg-type]


def test_ledger_snapshot_and_extensions() -> None:
    ledger = BudgetLedger(CFG, _Clock(50), started_at=0)
    snap = ledger.snapshot(10, 10, 1, 0.2)
    assert ledger.line(snap, "wall_clock_s").fraction == 0.5
    assert not ledger.would_exceed(snap, "wall_clock_s", 40)
    assert ledger.would_exceed(snap, "wall_clock_s", 60)
    assert ledger.llm_available(snap)
    extended = ledger.snapshot(10, 10, 2, 0.2, {"llm_calls": 3})
    assert ledger.line(extended, "llm_calls").limit == 5 and ledger.llm_available(extended)


RULES = default_rules(
    StopConfig(no_improvement_rounds=3, crash_limit=2, max_rounds=5), uses_llm=True
)


def test_nothing_fires_by_default() -> None:
    assert not evaluate(RULES, _ctx(_snap())).should_stop


def test_each_rule_fires() -> None:
    cases = {
        "user_stop": _ctx(_snap(), user_stop=True),
        "goal_reached": _ctx(_snap(), goal_reached=True),
        "wall_clock": _ctx(_snap(t=100)),
        "compute": _ctx(_snap(compute=400)),
        "env_steps": _ctx(_snap(steps=1000)),
        "llm_budget": _ctx(_snap(calls=2)),
        "crash_limit": _ctx(_snap(), unfixable_crashes=2),
        "no_improvement": _ctx(_snap(), rounds_without_improvement=3),
        "max_rounds": _ctx(_snap(), round_index=5),
    }
    for rule, context in cases.items():
        verdict = evaluate(RULES, context)
        assert verdict.should_stop and verdict.rule == rule, rule


def test_priority_and_round_settled_gating() -> None:
    both = _ctx(_snap(t=100), user_stop=True, goal_reached=True)
    assert evaluate(RULES, both).rule == "user_stop"
    unsettled = _ctx(_snap(), round_index=5, rounds_without_improvement=9, round_settled=False)
    assert not evaluate(RULES, unsettled).should_stop


def test_llm_budget_rule_is_optional() -> None:
    rules = default_rules(StopConfig(3, 2, 5, stop_on_llm_budget=False), uses_llm=True)
    context = _ctx(_snap(cost=1.0))
    assert not evaluate(rules, context).should_stop
    assert llm_budget_note(context) is not None
    deterministic = default_rules(StopConfig(3, 2, 5), uses_llm=False)
    assert not evaluate(deterministic, _ctx(_snap(calls=5))).should_stop
