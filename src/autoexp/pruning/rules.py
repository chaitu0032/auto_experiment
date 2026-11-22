"""Deterministic per-run rules: divergence and plateau."""

from __future__ import annotations

from autoexp.config.schema import DivergenceConfig, PlateauConfig
from autoexp.core.models import PruneVerdict
from autoexp.pruning.base import Cohort


class DivergencePruner:
    """Prune on NaN/inf, approx KL above kl_max for kl_patience updates, or value
    loss above value_loss_max."""

    name = "divergence"

    def __init__(self, config: DivergenceConfig) -> None:
        self._config = config

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        raise NotImplementedError


class PlateauPruner:
    """Prune when the best rolling return gained < min_delta over the last
    patience_steps, once past min_steps, unless the goal is reached (that is a
    SOLVED verdict instead)."""

    name = "plateau"

    def __init__(self, config: PlateauConfig) -> None:
        self._config = config

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        raise NotImplementedError
