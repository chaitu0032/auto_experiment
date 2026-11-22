"""Pruner interface and cohort model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from autoexp.core.models import Observation, PruneVerdict


@dataclass(frozen=True, slots=True)
class Cohort:
    """Live runs of one round, compared against each other."""

    round_index: int
    total_steps: int
    goal_threshold: float
    observations: tuple[Observation, ...]
    # "<run_id>:<rung>" pairs already kept at that rung; a rung decision is final.
    decided_rungs: frozenset[str] = frozenset()


@runtime_checkable
class Pruner(Protocol):
    name: str

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        """Return a verdict for every run the pruner has an opinion on."""
        ...
