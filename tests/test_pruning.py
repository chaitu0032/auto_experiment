"""Pruning rules on synthetic curves: divergence, plateau, halving/ASHA, pipeline."""

from __future__ import annotations

import pytest

from autoexp.config.schema import (
    DivergenceConfig,
    HalvingConfig,
    HalvingMode,
    LLMJudgeConfig,
    PlateauConfig,
)
from autoexp.core.models import PruneAction, RunPurpose, RunStatus
from autoexp.llm.client import LLMReply
from autoexp.pruning.base import Cohort, Pruner
from autoexp.pruning.halving import (
    SuccessiveHalvingPruner,
    keep_count,
    plan_hyperband,
    rung_steps,
)
from autoexp.pruning.llm_judge import LLMJudgePruner, parse_verdicts
from autoexp.pruning.pipeline import PruningPipeline
from autoexp.pruning.rules import DivergencePruner, PlateauPruner
from tests.conftest import observation, record, summary

DIV = DivergenceConfig(kl_max=0.5, kl_patience=3, value_loss_max=1e4)
PLAT = PlateauConfig(min_steps=1000, patience_steps=1000, min_delta=5.0)


def _cohort(*obs, total: int = 4000) -> Cohort:  # type: ignore[no-untyped-def]
    return Cohort(round_index=1, total_steps=total, goal_threshold=475.0, observations=tuple(obs))


def _linear(run: str, slope: float, until: int) -> object:
    return summary(run, [(s, slope * s / 100) for s in range(100, until + 1, 100)])


def test_divergence_rules() -> None:
    p = DivergencePruner(DIV)
    ok = summary("r0001", [(100, 10.0)])
    assert p.judge(ok).action is PruneAction.KEEP
    assert p.judge(summary("r0001", [(100, 10.0)], nan_seen=True)).action is PruneAction.PRUNE
    kl = p.judge(summary("r0001", [(100, 1.0)], kl_over_limit_streak=3, approx_kl=0.9))
    assert kl.action is PruneAction.PRUNE and "KL" in kl.reason
    vl = p.judge(summary("r0001", [(100, 1.0)], value_loss=2e4))
    assert vl.action is PruneAction.PRUNE and "value loss" in vl.reason


def test_plateau_prunes_flat_keeps_rising_and_solves() -> None:
    p = PlateauPruner(PLAT)
    flat = summary("r0001", [(s, 50.0) for s in range(100, 3001, 100)])
    assert p.judge(flat, 475).action is PruneAction.PRUNE
    rising = _linear("r0002", 1.0, 3000)
    assert p.judge(rising, 475).action is PruneAction.KEEP  # type: ignore[arg-type]
    early = summary("r0003", [(s, 50.0) for s in range(100, 901, 100)])
    assert p.judge(early, 475).action is PruneAction.KEEP
    solved = summary("r0004", [(100, 480.0)])
    assert p.judge(solved, 475).action is PruneAction.SOLVED


def test_rung_math() -> None:
    assert rung_steps(1000, (0.25, 0.5)) == (250, 500)
    assert keep_count(4, 2) == 2 and keep_count(3, 2) == 1 and keep_count(1, 3) == 1


def _halving(mode: HalvingMode, min_peers: int = 2) -> SuccessiveHalvingPruner:
    return SuccessiveHalvingPruner(
        HalvingConfig(mode=mode, rungs=(0.25, 0.5), eta=2, min_peers=min_peers)
    )


def test_sync_halving_waits_for_everyone_then_keeps_top_half() -> None:
    p = _halving(HalvingMode.SYNC)
    runs = [record(i) for i in range(1, 5)]
    slow = _cohort(
        *(
            observation(r, _linear(r.spec.run_id, i, 1500 if i < 4 else 900))  # type: ignore[arg-type]
            for i, r in enumerate(runs, 1)
        )
    )
    assert p.evaluate(slow) == (), "sync mode waits until every live run passed the rung"
    ready = _cohort(
        *(observation(r, _linear(r.spec.run_id, i, 1500)) for i, r in enumerate(runs, 1))
    )  # type: ignore[arg-type]
    pruned = {v.run_id for v in p.evaluate(ready) if v.action is PruneAction.PRUNE}
    assert pruned == {"r0001", "r0002"}


def test_halving_second_rung_and_evidence() -> None:
    p = _halving(HalvingMode.SYNC)
    runs = [record(i) for i in range(1, 5)]
    cohort = _cohort(
        *(observation(r, _linear(r.spec.run_id, i, 2100)) for i, r in enumerate(runs, 1))
    )  # type: ignore[arg-type]
    verdicts = p.evaluate(cohort)
    rung1 = [v for v in verdicts if v.evidence.get("rung") == 1]
    assert {v.run_id for v in rung1 if v.action is PruneAction.PRUNE} == {"r0003"}
    assert {v.run_id for v in rung1 if v.action is PruneAction.KEEP} == {"r0004"}
    assert rung1[0].evidence["of"] == 2


def test_async_halving_decides_with_min_peers() -> None:
    p = _halving(HalvingMode.ASYNC, min_peers=2)
    a, b, c = record(1), record(2), record(3)
    cohort = _cohort(
        observation(a, _linear("r0001", 3, 1100)),  # type: ignore[arg-type]
        observation(b, _linear("r0002", 1, 1100)),  # type: ignore[arg-type]
        observation(c, _linear("r0003", 9, 500)),  # type: ignore[arg-type]
    )
    verdicts = {v.run_id: v.action for v in p.evaluate(cohort) if v.evidence.get("rung") == 0}
    assert verdicts == {"r0001": PruneAction.KEEP, "r0002": PruneAction.PRUNE}


def test_halving_exempts_seed_checks_and_ignores_pruned_overshoot() -> None:
    p = _halving(HalvingMode.SYNC)
    checks = [record(i, purpose=RunPurpose.SEED_CHECK) for i in (1, 2)]
    cohort = _cohort(
        *(observation(r, _linear(r.spec.run_id, i, 1500)) for i, r in enumerate(checks, 1))
    )  # type: ignore[arg-type]
    assert p.evaluate(cohort) == ()
    pruned = record(3, RunStatus.PRUNED)
    alive = [record(4), record(5)]
    cohort = _cohort(
        observation(pruned, _linear("r0003", 9, 2100)),  # type: ignore[arg-type]
        *(observation(r, _linear(r.spec.run_id, i, 2100)) for i, r in enumerate(alive, 1)),  # type: ignore[arg-type]
    )
    rung1 = [v for v in p.evaluate(cohort) if v.evidence.get("rung") == 1]
    assert all(v.evidence["of"] == 2 for v in rung1)


def test_async_pool_keeps_pruned_runs_at_rungs_they_passed() -> None:
    p = _halving(HalvingMode.ASYNC, min_peers=2)
    a, b = record(1, RunStatus.COMPLETED), record(2, RunStatus.PRUNED)
    c, d = record(3), record(4)
    cohort = Cohort(
        1,
        4000,
        475.0,
        (
            observation(a, summary("r0001", [(1000, 100.0), (2000, 100.0)])),
            observation(b, summary("r0002", [(1000, 50.0), (2000, 50.0)])),
            observation(c, summary("r0003", [(1000, 70.0)])),
            observation(d, summary("r0004", [(1000, 60.0)])),
        ),
        decided_rungs=frozenset({"r0002:0"}),
    )
    rung0 = {v.run_id: v for v in p.evaluate(cohort) if v.evidence.get("rung") == 0}
    assert rung0["r0003"].action is PruneAction.KEEP and rung0["r0003"].evidence["of"] == 4
    assert rung0["r0004"].action is PruneAction.PRUNE


def test_hyperband_brackets() -> None:
    brackets = plan_hyperband(81, 3)
    assert brackets[0].n_configs == 81 and brackets[0].min_steps == 1
    assert brackets[-1].n_configs == 5 and brackets[-1].min_steps == 81
    with pytest.raises(ValueError):
        plan_hyperband(1, 3)


class _Boom:
    name = "boom"

    def evaluate(self, cohort: Cohort):  # type: ignore[no-untyped-def]
        raise RuntimeError("broken pruner")


def test_pipeline_first_non_keep_wins_and_only_running() -> None:
    running, done = record(1), record(2, RunStatus.COMPLETED)
    nan = summary("r0001", [(100, 10.0)], nan_seen=True)
    flat = summary("r0002", [(100, 10.0)], nan_seen=True)
    pipeline = PruningPipeline((_Boom(), DivergencePruner(DIV), PlateauPruner(PLAT)))  # type: ignore[arg-type]
    result = pipeline.run(_cohort(observation(running, nan), observation(done, flat)))
    assert set(result.final) == {"r0001"}
    assert result.final["r0001"].pruner == "divergence"
    assert all(v.run_id == "r0001" for v in result.verdicts)
    assert isinstance(DivergencePruner(DIV), Pruner)


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        self.calls += 1
        return LLMReply(self.text, 0.01, 10, 10, 0.1)


def test_llm_judge_seam() -> None:
    reply = (
        '{"verdicts": [{"run_id": "r0001", "action": "prune", "reason": "collapsing"},'
        ' {"run_id": "r9999", "action": "prune", "reason": "unknown"}]}'
    )
    llm = _FakeLLM(reply)
    judge = LLMJudgePruner(LLMJudgeConfig(enabled=True, every_ticks=2), llm)
    cohort = _cohort(observation(record(1), summary("r0001", [(100, 1.0)])))
    assert judge.evaluate(cohort) == () and llm.calls == 0
    verdicts = judge.evaluate(cohort)
    assert [(v.run_id, v.action) for v in verdicts] == [("r0001", PruneAction.PRUNE)]
    assert LLMJudgePruner(LLMJudgeConfig(enabled=False, every_ticks=1), llm).evaluate(cohort) == ()
    assert parse_verdicts({"verdicts": "nope"}, {"r0001"}) == ()
    bad = LLMJudgePruner(LLMJudgeConfig(enabled=True, every_ticks=1), _FakeLLM("no json"))
    assert bad.evaluate(cohort) == ()


def test_rung_decisions_are_final() -> None:
    """A run kept at a rung is not re-judged there when the peer pool changes later."""
    p = _halving(HalvingMode.SYNC)
    a, solved = record(1), record(2, RunStatus.SOLVED)
    cohort = Cohort(
        1,
        4000,
        475.0,
        (
            observation(a, _linear("r0001", 1, 1500)),  # type: ignore[arg-type]
            observation(solved, _linear("r0002", 9, 1500)),  # type: ignore[arg-type]
        ),
    )
    assert {v.run_id: v.action for v in p.evaluate(cohort)} == {"r0001": PruneAction.PRUNE}
    sticky = Cohort(1, 4000, 475.0, cohort.observations, decided_rungs=frozenset({"r0001:0"}))
    assert p.evaluate(sticky) == ()
