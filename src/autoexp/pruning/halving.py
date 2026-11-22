"""Successive halving (sync), ASHA (async), and a Hyperband bracket planner.

The value of a run at a rung is the best rolling return it reached by that
rung's step (robust to PPO's noisy returns).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from autoexp.config.schema import HalvingConfig, HalvingMode
from autoexp.core.models import Observation, PruneAction, PruneVerdict, RunPurpose, RunStatus
from autoexp.pruning.base import Cohort
from autoexp.telemetry.summary import best_until


def rung_steps(total_steps: int, rungs: tuple[float, ...]) -> tuple[int, ...]:
    """Absolute step counts of the rungs."""
    return tuple(int(total_steps * r) for r in rungs)


def keep_count(n: int, eta: int) -> int:
    """How many of `n` runs survive a rung: max(1, n // eta)."""
    return max(1, n // eta)


def _value_at(observation: Observation, step: int) -> float:
    if observation.summary is None:
        return -math.inf
    value = best_until(observation.summary, step)
    return value if value is not None else -math.inf


def _passed(observation: Observation, step: int) -> bool:
    summary = observation.summary
    return summary is not None and summary.step >= step


class SuccessiveHalvingPruner:
    """sync: once every live run in the cohort passed rung r, keep the top
    keep_count by the value recorded at r and prune the rest.
    async (ASHA): decide each run as it reaches rung r against the values
    recorded at r so far, once at least min_peers have reported.

    The cohort must include terminal runs of the round too (they carry their
    rung values); only RUNNING runs ever receive a PRUNE verdict. Seed-check runs
    are exempt: they verify a winner, they do not compete.
    """

    name = "halving"

    def __init__(self, config: HalvingConfig) -> None:
        self._config = config

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        competing = tuple(
            o for o in cohort.observations if o.record.spec.purpose is not RunPurpose.SEED_CHECK
        )
        cohort = Cohort(
            round_index=cohort.round_index,
            total_steps=cohort.total_steps,
            goal_threshold=cohort.goal_threshold,
            observations=competing,
            decided_rungs=cohort.decided_rungs,
            pruned_rungs=cohort.pruned_rungs,
        )
        verdicts: list[PruneVerdict] = []
        alive = {
            o.record.spec.run_id
            for o in cohort.observations
            if o.record.status in (RunStatus.RUNNING, RunStatus.ASSIGNED, RunStatus.ALLOCATED)
        }
        for rung_index, step in enumerate(rung_steps(cohort.total_steps, self._config.rungs)):
            rung_verdicts = self._rung(cohort, rung_index, step, alive)
            verdicts.extend(rung_verdicts)
            alive -= {v.run_id for v in rung_verdicts if v.action is PruneAction.PRUNE}
        return tuple(verdicts)

    def _counts_pruned_at(self, cohort: Cohort, key: str) -> bool:
        return key in cohort.decided_rungs or (
            self._config.mode is HalvingMode.ASYNC and key in cohort.pruned_rungs
        )

    def _rung(
        self, cohort: Cohort, rung_index: int, step: int, alive: set[str]
    ) -> list[PruneVerdict]:
        # Peers: runs still alive, plus finished runs that got past this rung on
        # their own merit. A pruned run counts at rungs it was kept at and, in async
        # mode, at the rung that pruned it; its overshoot past that rung never counts.
        peers = [
            o
            for o in cohort.observations
            if o.record.spec.run_id in alive
            or (
                o.record.status.is_terminal
                and (
                    o.record.status is not RunStatus.PRUNED
                    or self._counts_pruned_at(cohort, f"{o.record.spec.run_id}:{rung_index}")
                )
                and _passed(o, step)
            )
        ]
        reported = [o for o in peers if _passed(o, step)]
        if self._config.mode is HalvingMode.SYNC:
            if len(reported) < len(peers) or len(reported) < self._config.min_peers:
                return []
            pool = reported
        else:
            if len(reported) < self._config.min_peers:
                return []
            pool = reported
        ranked = sorted(pool, key=lambda o: _value_at(o, step), reverse=True)
        keep = keep_count(len(ranked), self._config.eta)
        verdicts = []
        for rank, o in enumerate(ranked):
            rid = o.record.spec.run_id
            if rid not in alive or f"{rid}:{rung_index}" in cohort.decided_rungs:
                continue
            value = _value_at(o, step)
            evidence = {
                "rung": rung_index,
                "rung_step": step,
                "rank": rank + 1,
                "of": len(ranked),
                "keep": keep,
                "value": None if math.isinf(value) else round(value, 2),
            }
            if rank < keep:
                verdicts.append(
                    PruneVerdict(
                        run_id=rid,
                        pruner=self.name,
                        action=PruneAction.KEEP,
                        reason=f"rung {rung_index} ({step} steps): rank {rank + 1}/{len(ranked)}",
                        evidence=evidence,
                    )
                )
            else:
                verdicts.append(
                    PruneVerdict(
                        run_id=rid,
                        pruner=self.name,
                        action=PruneAction.PRUNE,
                        reason=(
                            f"successive halving rung {rung_index} ({step} steps): rank "
                            f"{rank + 1}/{len(ranked)}, only top {keep} continue"
                        ),
                        evidence=evidence,
                    )
                )
        return verdicts


@dataclass(frozen=True, slots=True)
class Bracket:
    index: int
    n_configs: int
    min_steps: int


def plan_hyperband(max_steps: int, eta: int) -> tuple[Bracket, ...]:
    """Hyperband brackets (n configs, starting budget) for the decision engine.

    With s_max = floor(log_eta(max_steps / min_unit)) where the minimum unit is
    max_steps / eta**s_max, bracket s starts n = ceil((s_max+1)/(s+1) * eta**s)
    configs at max_steps * eta**-s steps.
    """
    if eta < 2 or max_steps < eta:
        raise ValueError("need eta >= 2 and max_steps >= eta")
    s_max = int(math.log(max_steps, eta) // 1)
    s_max = min(s_max, 4)  # keep brackets practical on one machine
    brackets = []
    for s in range(s_max, -1, -1):
        n = math.ceil((s_max + 1) / (s + 1) * eta**s)
        brackets.append(Bracket(index=s_max - s, n_configs=n, min_steps=int(max_steps * eta**-s)))
    return tuple(brackets)
