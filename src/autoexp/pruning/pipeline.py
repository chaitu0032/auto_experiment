"""Run pruners in configured order; first PRUNE/SOLVED verdict per run wins.

All verdicts, including KEEP, are returned so the master can log the reasoning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.core.ids import RunId
from autoexp.core.models import PruneVerdict
from autoexp.pruning.base import Cohort, Pruner


@dataclass(frozen=True, slots=True)
class PipelineResult:
    verdicts: tuple[PruneVerdict, ...]  # every verdict, in evaluation order
    final: Mapping[RunId, PruneVerdict]  # the deciding verdict per run


class PruningPipeline:
    def __init__(self, pruners: tuple[Pruner, ...]) -> None:
        self._pruners = pruners

    def run(self, cohort: Cohort) -> PipelineResult:
        raise NotImplementedError
