"""Budget ledger: meters wall clock, compute seconds, env steps, LLM calls, cost.

Usage is recomputed from disk each tick (run events and telemetry for compute
and steps, master events for LLM calls), so the ledger survives restarts.
"""

from __future__ import annotations

from typing import Protocol

from autoexp.config.schema import BudgetConfig
from autoexp.core.models import BudgetSnapshot


class Clock(Protocol):
    def now(self) -> float: ...


class BudgetLedger:
    def __init__(self, config: BudgetConfig, clock: Clock, started_at: float) -> None:
        self._config = config
        self._clock = clock
        self._started_at = started_at

    def snapshot(
        self, compute_s: float, env_steps: int, llm_calls: int, llm_cost_usd: float
    ) -> BudgetSnapshot:
        raise NotImplementedError

    def would_exceed(self, snapshot: BudgetSnapshot, name: str, extra: float) -> bool:
        """True if spending `extra` more of budget `name` passes its limit; the
        master files a budget_extension approval instead of overrunning."""
        raise NotImplementedError
