"""Read models for the dashboard, built from files on each request."""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.core.models import (
    ApprovalRequest,
    BudgetSnapshot,
    CurveSummary,
    Finding,
    MasterEvent,
    RunRecord,
    WorkerRecord,
)
from autoexp.state.layout import StateLayout


@dataclass(frozen=True, slots=True)
class Overview:
    experiment_id: str
    round_index: int
    runs: tuple[RunRecord, ...]
    summaries: tuple[CurveSummary, ...]
    workers: tuple[WorkerRecord, ...]
    events: tuple[MasterEvent, ...]
    budget: BudgetSnapshot | None
    approvals: tuple[ApprovalRequest, ...]
    finding: Finding | None


def build_overview(layout: StateLayout) -> Overview:
    raise NotImplementedError
