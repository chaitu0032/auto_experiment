"""LLM-judgement pruner (seam 2).

Every `every_ticks` it sends compact curve summaries and log excerpts to the
LLM and asks for keep/prune verdicts as JSON. Output is validated (known run
IDs only, action in {keep, prune}, reason required); invalid output yields no
verdicts. Each call goes through the metered client, so it is counted by the
budget ledger and refused once the LLM budget is spent.
"""

from __future__ import annotations

import json
import logging

from autoexp.config.schema import LLMJudgeConfig
from autoexp.core.models import PruneAction, PruneVerdict, RunStatus
from autoexp.llm.client import LLMClient, LLMError, extract_json
from autoexp.pruning.base import Cohort

log = logging.getLogger(__name__)

PROMPT = """You are the pruning judge of an RL experiment. Goal: rolling mean episode
return over the last 100 episodes >= {goal}. Each run trains up to {total} steps.
Prune a run only when its curves or logs show it will clearly not be competitive
(for example collapsing returns, exploding losses, a crash loop). When unsure, keep.

Runs (JSON):
{runs}

Reply with JSON only: {{"verdicts": [{{"run_id": "...", "action": "keep"|"prune",
"reason": "one sentence"}}]}}"""


class LLMJudgePruner:
    name = "llm_judge"

    def __init__(self, config: LLMJudgeConfig, client: LLMClient) -> None:
        self._config = config
        self._client = client
        self._calls = 0  # tick counter: judge every `every_ticks` evaluations

    def evaluate(self, cohort: Cohort) -> tuple[PruneVerdict, ...]:
        if not self._config.enabled:
            return ()
        self._calls += 1
        if self._calls % self._config.every_ticks != 0:
            return ()
        running = [o for o in cohort.observations if o.record.status is RunStatus.RUNNING]
        if not running:
            return ()
        prompt = PROMPT.format(
            goal=cohort.goal_threshold,
            total=cohort.total_steps,
            runs=json.dumps([_describe(o) for o in running], indent=1),
        )
        try:
            reply = self._client.complete(prompt, purpose="llm_judge")
            return parse_verdicts(extract_json(reply.text), {o.record.spec.run_id for o in running})
        except LLMError as err:
            log.warning("LLM judge skipped: %s", err)
            return ()


def _describe(o) -> dict[str, object]:  # type: ignore[no-untyped-def]
    s = o.summary
    curve = list(s.curve[-20:]) if s else []
    return {
        "run_id": o.record.spec.run_id,
        "config": dict(o.record.spec.config),
        "step": s.step if s else 0,
        "rolling_return": s.rolling_return if s else None,
        "best_rolling_return": s.best_rolling_return if s else None,
        "approx_kl": s.approx_kl if s else None,
        "entropy": s.entropy if s else None,
        "value_loss": s.value_loss if s else None,
        "recent_curve": [[st, round(r, 1)] for st, r in curve],
        "traceback": (o.traceback or "")[-400:] or None,
    }


def parse_verdicts(raw: dict[str, object], known: set[str]) -> tuple[PruneVerdict, ...]:
    items = raw.get("verdicts")
    if not isinstance(items, list):
        return ()
    verdicts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rid, action, reason = item.get("run_id"), item.get("action"), item.get("reason")
        if rid not in known or action not in ("keep", "prune") or not isinstance(reason, str):
            continue
        verdicts.append(
            PruneVerdict(
                run_id=str(rid),
                pruner="llm_judge",
                action=PruneAction(action),
                reason=f"LLM judge: {reason.strip()[:300]}",
            )
        )
    return tuple(verdicts)
