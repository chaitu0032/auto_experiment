"""Decision engines: verification protocol, exploration, LLM validation and fallback."""

from __future__ import annotations

import json

import pytest

from autoexp.config.schema import GoalConfig, ParamKind, ParamSpace
from autoexp.core.models import BudgetSnapshot, DecisionAction, RunPurpose, RunStatus
from autoexp.decision.base import DecisionContext, config_key, standings, winner
from autoexp.decision.deterministic import DeterministicDecisionEngine, mutate
from autoexp.decision.llm import LLMDecisionEngine, build_prompt
from autoexp.decision.validate import InvalidDecision, clamp_config, parse_exploration
from autoexp.llm.client import LLMError, LLMReply
from tests.conftest import record, summary

SPACE = {
    "learning_rate": ParamSpace("learning_rate", ParamKind.LOG_FLOAT, 1e-4, 3e-3),
    "num_envs": ParamSpace("num_envs", ParamKind.CHOICE, values=(4, 8)),
    "ent_coef": ParamSpace("ent_coef", ParamKind.FLOAT, 0.0, 0.02),
    "update_epochs": ParamSpace("update_epochs", ParamKind.INT, 2, 10),
}
GOAL = GoalConfig(metric="rolling_return", window=100, threshold=475.0, seeds_required=3)
BASE = {"learning_rate": 2.5e-4, "num_envs": 4, "ent_coef": 0.01, "update_epochs": 4}


def _ctx(runs, summaries, free: int = 4) -> DecisionContext:  # type: ignore[no-untyped-def]
    return DecisionContext(
        round_index=2,
        free_slots=free,
        goal=GOAL,
        search_space=SPACE,
        runs=tuple(runs),
        summaries=summaries,
        prunes=(),
        budget=BudgetSnapshot(0.0, ()),
        rounds_without_improvement=0,
    )


def _run(
    n: int,
    status: RunStatus,
    best: float,
    *,
    config=None,
    seed: int = 1,
    purpose: RunPurpose = RunPurpose.EXPLORE,
):  # type: ignore[no-untyped-def]
    rec = record(
        n,
        status,
        config=dict(config or {**BASE, "learning_rate": 1e-4 * n}),
        seed=seed,
        purpose=purpose,
    )
    return rec, summary(rec.spec.run_id, [(1000, best)])


def test_explore_mutates_best_configs_into_untried_ones() -> None:
    pairs = [_run(i, RunStatus.COMPLETED, 100.0 * i) for i in range(1, 5)]
    ctx = _ctx([p[0] for p in pairs], {p[0].spec.run_id: p[1] for p in pairs})
    decision = DeterministicDecisionEngine().decide(ctx)
    assert decision.action is DecisionAction.LAUNCH and len(decision.proposals) == 4
    tried = {config_key(p[0].spec.config) for p in pairs}
    keys = {config_key(p.config) for p in decision.proposals}
    assert len(keys) == 4 and not keys & tried
    assert {p.parent_run_id for p in decision.proposals} <= {"r0004", "r0003"}
    assert all(p.seed == 1 and p.purpose is RunPurpose.FORK for p in decision.proposals)
    for p in decision.proposals:
        assert clamp_config(p.config, SPACE) == dict(sorted(p.config.items()))


def test_solved_config_is_verified_on_new_seeds_then_wins() -> None:
    solved, s1 = _run(1, RunStatus.SOLVED, 480.0, config=BASE)
    other, s2 = _run(2, RunStatus.PRUNED, 90.0)
    ctx = _ctx([solved, other], {"r0001": s1, "r0002": s2})
    decision = DeterministicDecisionEngine().decide(ctx)
    assert decision.action is DecisionAction.VERIFY_SEEDS
    assert [p.seed for p in decision.proposals] == [2, 3]
    assert all(
        p.purpose is RunPurpose.SEED_CHECK and p.parent_run_id == "r0001"
        for p in decision.proposals
    )
    checks = [
        _run(n, RunStatus.SOLVED, 490.0, config=BASE, seed=seed, purpose=RunPurpose.SEED_CHECK)
        for n, seed in ((3, 2), (4, 3))
    ]
    ctx = _ctx(
        [solved, other, *(c[0] for c in checks)],
        {"r0001": s1, "r0002": s2, **{c[0].spec.run_id: c[1] for c in checks}},
    )
    assert winner(ctx) is not None
    assert DeterministicDecisionEngine().decide(ctx).action is DecisionAction.STOP


def test_failed_seed_check_refutes_the_candidate() -> None:
    solved, s1 = _run(1, RunStatus.SOLVED, 480.0, config=BASE)
    failed, s2 = _run(
        2, RunStatus.COMPLETED, 300.0, config=BASE, seed=2, purpose=RunPurpose.SEED_CHECK
    )
    ctx = _ctx([solved, failed], {"r0001": s1, "r0002": s2})
    (standing,) = standings(ctx)
    assert standing.refuted and winner(ctx) is None
    assert DeterministicDecisionEngine().decide(ctx).action is DecisionAction.LAUNCH


def test_live_seed_checks_are_not_duplicated() -> None:
    solved, s1 = _run(1, RunStatus.SOLVED, 480.0, config=BASE)
    live, s2 = _run(2, RunStatus.RUNNING, 300.0, config=BASE, seed=2, purpose=RunPurpose.SEED_CHECK)
    decision = DeterministicDecisionEngine().decide(
        _ctx([solved, live], {"r0001": s1, "r0002": s2})
    )
    assert decision.action is DecisionAction.VERIFY_SEEDS and [
        p.seed for p in decision.proposals
    ] == [3]


def test_no_free_slots_waits() -> None:
    rec, s = _run(1, RunStatus.COMPLETED, 10.0)
    assert (
        DeterministicDecisionEngine().decide(_ctx([rec], {"r0001": s}, free=0)).action
        is DecisionAction.WAIT
    )


def test_mutate_stays_in_space() -> None:
    import random

    rng = random.Random(0)
    for _ in range(200):
        child = mutate(BASE, SPACE, rng)
        assert clamp_config(child, SPACE)


def test_validation_rejects_and_clamps() -> None:
    assert clamp_config({**BASE, "learning_rate": 1.0}, SPACE)["learning_rate"] == 3e-3
    assert clamp_config({**BASE, "update_epochs": 3.7}, SPACE)["update_epochs"] == 4
    for bad in (
        {**BASE, "extra": 1},
        {"learning_rate": 1e-3},
        {**BASE, "num_envs": 5},
        {**BASE, "ent_coef": "x"},
        {**BASE, "ent_coef": float("nan")},
    ):
        with pytest.raises(InvalidDecision):
            clamp_config(bad, SPACE)
    rec, s = _run(1, RunStatus.COMPLETED, 10.0, config=BASE)
    ctx = _ctx([rec], {"r0001": s}, free=1)
    with pytest.raises(InvalidDecision):
        parse_exploration({"proposals": [{"config": BASE}]}, ctx, "llm", [1])  # duplicate only
    with pytest.raises(InvalidDecision):
        parse_exploration({"proposals": [], "rationale": "x"}, ctx, "llm", [1])
    with pytest.raises(InvalidDecision):
        parse_exploration({"proposals": [{"config": BASE}], "evil": 1}, ctx, "llm", [1])
    ok = parse_exploration(
        {
            "proposals": [
                {
                    "config": {**BASE, "num_envs": 8},
                    "parent_run_id": "r0001",
                    "rationale": "more envs",
                },
                {"config": {**BASE, "num_envs": 8, "ent_coef": 0.0}},
            ],
            "rationale": "why",
            "code_changes": [{"summary": "norm obs", "diff": "--- a"}],
        },
        ctx,
        "llm",
        [1],
    )
    assert len(ok.proposals) == 1 and ok.proposals[0].parent_run_id == "r0001"
    assert ok.code_changes[0].summary == "norm obs"


class _LLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMReply(self.reply, 0.02, 100, 50, 1.0)


def test_llm_engine_uses_valid_reply() -> None:
    rec, s = _run(1, RunStatus.COMPLETED, 200.0, config=BASE)
    proposal = {
        "config": {**BASE, "learning_rate": 1e-3},
        "parent_run_id": "r0001",
        "rationale": "faster",
    }
    llm = _LLM(
        "```json\n" + json.dumps({"proposals": [proposal], "rationale": "raise lr"}) + "\n```"
    )
    decision = LLMDecisionEngine(llm, DeterministicDecisionEngine()).decide(
        _ctx([rec], {"r0001": s})
    )
    assert decision.engine == "llm" and decision.fallback_reason is None
    assert decision.proposals[0].config["learning_rate"] == 1e-3
    assert "r0001" in llm.prompts[0] and "learning_rate" in build_prompt(_ctx([rec], {"r0001": s}))


@pytest.mark.parametrize(
    "reply", ["not json", '{"proposals": [{"config": {"bogus": 1}}]}', LLMError("down")]
)
def test_llm_engine_falls_back(reply: str | Exception) -> None:
    rec, s = _run(1, RunStatus.COMPLETED, 200.0, config=BASE)
    decision = LLMDecisionEngine(_LLM(reply), DeterministicDecisionEngine()).decide(
        _ctx([rec], {"r0001": s})
    )
    assert decision.engine == "deterministic" and decision.fallback_reason
    assert decision.action is DecisionAction.LAUNCH


def test_llm_engine_never_overrides_the_protocol() -> None:
    solved, s1 = _run(1, RunStatus.SOLVED, 480.0, config=BASE)
    llm = _LLM("{}")
    decision = LLMDecisionEngine(llm, DeterministicDecisionEngine()).decide(
        _ctx([solved], {"r0001": s1})
    )
    assert decision.action is DecisionAction.VERIFY_SEEDS and llm.prompts == []


def test_narrative() -> None:
    rec, s = _run(1, RunStatus.COMPLETED, 200.0)
    engine = LLMDecisionEngine(_LLM("It worked because."), DeterministicDecisionEngine())
    assert engine.narrate(_ctx([rec], {"r0001": s}), "goal") == "It worked because."
    failing = LLMDecisionEngine(_LLM(LLMError("x")), DeterministicDecisionEngine())
    assert failing.narrate(_ctx([rec], {"r0001": s}), "goal") is None
    assert DeterministicDecisionEngine().narrate(_ctx([rec], {"r0001": s}), "goal") is None
