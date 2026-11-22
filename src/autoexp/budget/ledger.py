"""Budget ledger: meters wall clock, compute seconds, env steps, LLM calls, cost.

Usage is recomputed from disk each tick (run events and telemetry for compute
and steps, master events for LLM calls), so the ledger survives restarts.
Approved budget extensions raise a limit; they are passed in, not stored here.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Protocol

from autoexp.config.schema import BudgetConfig
from autoexp.core.models import BudgetLine, BudgetSnapshot

BUDGET_NAMES = ("wall_clock_s", "compute_s", "env_steps", "llm_calls", "llm_cost_usd")


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class BudgetLedger:
    def __init__(self, config: BudgetConfig, clock: Clock, started_at: float) -> None:
        self._config = config
        self._clock = clock
        self._started_at = started_at

    @property
    def started_at(self) -> float:
        return self._started_at

    def limits(self, extensions: Mapping[str, float] | None = None) -> dict[str, float]:
        extra = extensions or {}
        return {
            name: float(getattr(self._config, name)) + extra.get(name, 0.0) for name in BUDGET_NAMES
        }

    def snapshot(
        self,
        compute_s: float,
        env_steps: int,
        llm_calls: int,
        llm_cost_usd: float,
        extensions: Mapping[str, float] | None = None,
    ) -> BudgetSnapshot:
        now = self._clock.now()
        used = {
            "wall_clock_s": max(0.0, now - self._started_at),
            "compute_s": compute_s,
            "env_steps": float(env_steps),
            "llm_calls": float(llm_calls),
            "llm_cost_usd": llm_cost_usd,
        }
        limits = self.limits(extensions)
        return BudgetSnapshot(
            at=now,
            lines=tuple(BudgetLine(name=n, used=used[n], limit=limits[n]) for n in BUDGET_NAMES),
        )

    @staticmethod
    def line(snapshot: BudgetSnapshot, name: str) -> BudgetLine:
        for line in snapshot.lines:
            if line.name == name:
                return line
        raise KeyError(name)

    def would_exceed(self, snapshot: BudgetSnapshot, name: str, extra: float) -> bool:
        """True if spending `extra` more of budget `name` passes its limit; the
        master files a budget_extension approval instead of overrunning."""
        line = self.line(snapshot, name)
        return line.used + extra > line.limit

    def llm_available(self, snapshot: BudgetSnapshot) -> bool:
        calls = self.line(snapshot, "llm_calls")
        cost = self.line(snapshot, "llm_cost_usd")
        return calls.used < calls.limit and cost.used < cost.limit
