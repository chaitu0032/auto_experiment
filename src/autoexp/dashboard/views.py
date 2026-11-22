"""Pure builders from state files to API DTOs (used by handlers.py).

They fold run events, read cached curve summaries and the master event log,
and compute `version` from the sizes and mtimes of the files the overview
depends on, so the SSE stream can tell when anything changed.
"""

from __future__ import annotations

import time
import zlib
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from autoexp.core.models import RunRecord, WorkerRecord
from autoexp.dashboard.schemas import RunRowDTO, WorkerDTO
from autoexp.state.jsonl import read_json
from autoexp.state.layout import StateLayout
from autoexp.state.locks import FlockLockManager

MASTER_STALE_S = 60.0


def _watched(layout: StateLayout) -> Iterator[Path]:
    yield from (
        layout.master_events,
        layout.master_budget,
        layout.master_summaries,
        layout.finding_json,
        layout.stop_flag,
        layout.master_heartbeat,
    )
    for directory, pattern in (
        (layout.runs_dir, "*/events.jsonl"),
        (layout.approvals_dir, "*.json"),
        (layout.workers_dir, "*/events.jsonl"),
        (layout.workers_dir, "*/takeover"),
        (layout.workers_dir, "*/pane.json"),
    ):
        if directory.exists():
            yield from sorted(directory.glob(pattern))


def overview_version(layout: StateLayout) -> int:
    parts = []
    for path in _watched(layout):
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        parts.append(f"{path.name}:{st.st_size}:{st.st_mtime_ns}")
    return zlib.crc32("|".join(parts).encode()) & 0x7FFFFFFF


def master_status(layout: StateLayout) -> str:
    beat = read_json(layout.master_heartbeat)
    if isinstance(beat, dict) and beat.get("status") == "finished":
        return "stopped"
    if FlockLockManager().probe(layout.master_lock):
        return "running"
    if isinstance(beat, dict) and time.time() - float(beat.get("at", 0)) < MASTER_STALE_S:
        return "running"
    return "absent" if beat is None else "stopped"


def summaries(layout: StateLayout) -> Mapping[str, Mapping[str, Any]]:
    raw = read_json(layout.master_summaries)
    return raw if isinstance(raw, dict) else {}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def run_row(record: RunRecord, summary: Mapping[str, Any] | None) -> RunRowDTO:
    s = summary or {}
    return RunRowDTO(
        run_id=record.spec.run_id,
        round_index=record.spec.round_index,
        purpose=record.spec.purpose.value,
        parent_run_id=record.spec.parent_run_id,
        status=record.status.value,
        worker_id=record.worker_id,
        device_id=record.device_id,
        seed=record.spec.seed,
        config=dict(record.spec.config),
        step=int(s.get("step") or 0),
        total_steps=record.spec.total_steps,
        rolling_return=_num(s.get("rolling_return")),
        best_rolling_return=_num(s.get("best_rolling_return")),
        sps=_num(s.get("sps")),
        stop_reason=record.stop_reason,
        started_at=record.started_at,
        ended_at=record.ended_at,
    )


def worker_row(record: WorkerRecord, run_ids: list[str]) -> WorkerDTO:
    return WorkerDTO(
        worker_id=record.worker_id,
        pane_id=record.pane_id,
        runtime=record.runtime,
        state=record.state.value,
        takeover=record.takeover,
        run_ids=run_ids,
    )
