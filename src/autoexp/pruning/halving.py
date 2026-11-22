"""Successive halving (sync), ASHA (async), and a Hyperband bracket planner."""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.config.schema import HalvingConfig
from autoexp.core.models import PruneVerdict
from autoexp.pruning.base import Cohort


def rung_steps(total_steps: int, rungs: tuple[float, ...]) -> tuple[int, ...]:
    """Absolute step counts of the rungs."""
    raise NotImplementedError


def keep_count(n: int, eta: int) -> int:
    """How many of `n` runs survive a rung: max(1, n // eta)."""
    raise NotImplementedError


class SuccessiveHalvingPruner:
    """sync: once every live run in the cohort passed rung r, keep the top
    keep_count by the value recorded at r and prune the rest.
    async (ASHA): decide each run as it reaches rung r against the values
    recorded at r so far, once at least min_peers have reported."""

    name = "halving"

    def __init__(self, config: HalvingConfig) -> None:
        self._config = config

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Bracket:
    index: int
    n_configs: int
    min_steps: int


def plan_hyperband(max_steps: int, eta: int) -> tuple[Bracket, ...]:
    """Hyperband brackets (n configs, starting budget) for the decision engine."""
    raise NotImplementedError
