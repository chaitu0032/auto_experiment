"""Deterministic decision engine; also the fallback for the LLM engine.

Order of rules:
1. A config holds the goal on `seeds_required` seeds -> STOP (goal reached).
2. A config solved on fewer seeds -> VERIFY_SEEDS with unused seeds.
3. Otherwise -> LAUNCH mutations of the top-k configs (scaled learning rate,
   neighbouring choices, nudged coefficients) inside the search space, skipping
   configs already tried, up to `free_slots` proposals.
"""

from __future__ import annotations

from autoexp.core.models import Decision
from autoexp.decision.base import DecisionContext


class DeterministicDecisionEngine:
    name = "deterministic"

    def __init__(self, top_k: int = 2, seed_base: int = 1) -> None:
        self._top_k = top_k
        self._seed_base = seed_base

    def decide(self, context: DecisionContext) -> Decision:
        raise NotImplementedError
