"""Pure builders from state files to API DTOs (used by handlers.py).

They fold run events, read cached curve summaries and the master event log,
and compute `version` from the sizes and mtimes of the files the overview
depends on, so the SSE stream can tell when anything changed.
"""

from __future__ import annotations

from autoexp.core.models import CurveSummary, RunRecord, WorkerRecord
from autoexp.dashboard.schemas import RunRowDTO, WorkerDTO
from autoexp.state.layout import StateLayout


def overview_version(layout: StateLayout) -> int:
    raise NotImplementedError


def run_row(record: RunRecord, summary: CurveSummary | None) -> RunRowDTO:
    raise NotImplementedError


def worker_row(record: WorkerRecord) -> WorkerDTO:
    raise NotImplementedError
