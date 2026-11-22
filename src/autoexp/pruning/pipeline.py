"""Run pruners in configured order; first PRUNE/SOLVED verdict per run wins.

All verdicts, including KEEP, are returned so the master can log the reasoning.
Only runs that are RUNNING can be pruned or solved; verdicts on other runs are
dropped (terminal runs are in the cohort only as halving peers).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.core.ids import RunId
from autoexp.core.models import PruneAction, PruneVerdict, RunStatus
from autoexp.pruning.base import Cohort, Pruner

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    verdicts: tuple[PruneVerdict, ...]  # every verdict, in evaluation order
    final: Mapping[RunId, PruneVerdict]  # the deciding PRUNE/SOLVED verdict per run


class PruningPipeline:
    def __init__(self, pruners: tuple[Pruner, ...]) -> None:
        self._pruners = pruners

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self._pruners)

    def run(self, cohort: Cohort) -> PipelineResult:
        running = {
            o.record.spec.run_id
            for o in cohort.observations
            if o.record.status is RunStatus.RUNNING
        }
        verdicts: list[PruneVerdict] = []
        final: dict[RunId, PruneVerdict] = {}
        for pruner in self._pruners:
            try:
                produced = pruner.evaluate(cohort)
            except Exception:  # a broken pruner must not stop the loop
                log.exception("pruner %s failed", pruner.name)
                continue
            for verdict in produced:
                rid = RunId(verdict.run_id)
                if rid not in running:
                    continue
                verdicts.append(verdict)
                if rid not in final and verdict.action is not PruneAction.KEEP:
                    final[rid] = verdict
        return PipelineResult(verdicts=tuple(verdicts), final=final)
