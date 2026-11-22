"""LLM-judgement pruner (seam 2).

Every `every_ticks` it sends compact curve summaries and log excerpts to the
LLM and asks for keep/prune verdicts as JSON. Output is validated (known run
IDs only, action in {keep, prune}, reason required); invalid output yields no
verdicts. Each call is metered by the budget ledger and skipped once the LLM
budget is spent.
"""

from __future__ import annotations

from autoexp.config.schema import LLMJudgeConfig
from autoexp.core.models import PruneVerdict
from autoexp.llm.client import LLMClient
from autoexp.pruning.base import Cohort


class LLMJudgePruner:
    name = "llm_judge"

    def __init__(self, config: LLMJudgeConfig, client: LLMClient) -> None:
        self._config = config
        self._client = client

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        raise NotImplementedError
