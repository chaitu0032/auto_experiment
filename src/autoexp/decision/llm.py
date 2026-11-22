"""LLM decision engine (seam 1).

Builds a prompt from the DecisionContext (search space, run table, curve
summaries, prune history, budgets, goal) asking for a JSON object:
`{action, proposals[{config, seed, purpose, parent_run_id, rationale}],
rationale, code_changes[{summary, diff}], narrative}`. The reply goes through
`decision/validate.py`; any error (timeout, bad JSON, invalid proposal, LLM
budget spent) falls back to the deterministic engine and records why.
Code changes never execute here: the master turns them into approvals.
"""

from __future__ import annotations

from autoexp.core.models import Decision
from autoexp.decision.base import DecisionContext, DecisionEngine
from autoexp.llm.client import LLMClient


class LLMDecisionEngine:
    name = "llm"

    def __init__(self, client: LLMClient, fallback: DecisionEngine) -> None:
        self._client = client
        self._fallback = fallback

    def decide(self, context: DecisionContext) -> Decision:
        raise NotImplementedError


def build_prompt(context: DecisionContext) -> str:
    raise NotImplementedError
