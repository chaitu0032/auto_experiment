"""Run registry: allocate run IDs, write specs, append and fold run events.

The registry is the only writer of `counters.json` and `spec.json`; the master
is its only allocating caller. The current `RunRecord` of a run is always
`fold(spec, events)`; it is never stored on its own, so it cannot drift from the
event log.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Protocol

from autoexp.core.ids import DeviceId, RunId, WorkerId, format_run_id, parse_run_id
from autoexp.core.models import (
    EventSource,
    ProposedRun,
    RunEvent,
    RunEventKind,
    RunRecord,
    RunSpec,
    RunStatus,
)
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import append_line, read_json, read_lines, write_json_atomic
from autoexp.state.layout import StateLayout

OUTCOME_STATUS = {
    "completed": RunStatus.COMPLETED,
    "solved": RunStatus.SOLVED,
    "pruned": RunStatus.PRUNED,
    "stopped": RunStatus.STOPPED,
    "crashed": RunStatus.CRASHED,
}


class RegistryError(RuntimeError):
    pass


class RunRegistry(Protocol):
    def allocate(self, proposal: ProposedRun, round_index: int) -> RunSpec:
        """Take the next run ID under the counters lock, write spec.json and an
        `allocated` event, and return the spec."""
        ...

    def append(self, event: RunEvent) -> None: ...

    def get(self, run_id: RunId) -> RunRecord | None: ...

    def all(self) -> tuple[RunRecord, ...]: ...


def fold(spec: RunSpec, events: Iterable[RunEvent]) -> RunRecord:
    """Pure: derive the current RunRecord from a spec and its ordered events.

    Rules: `assigned` sets worker and device; `launched`/`started` move to
    RUNNING; `exited` maps exit outcome to SOLVED/COMPLETED/PRUNED/STOPPED/
    CRASHED; `lost` moves to LOST; events after a terminal status are ignored
    except for counting crashes.
    """
    record = RunRecord(
        spec=spec,
        status=RunStatus.ALLOCATED,
        worker_id=None,
        device_id=None,
        allocated_at=0.0,
    )
    for event in events:
        record = _apply(record, event)
    return record


def _apply(record: RunRecord, event: RunEvent) -> RunRecord:
    data = event.data
    if event.kind is RunEventKind.EXITED and data.get("outcome") == "crashed":
        record = replace(record, crash_count=record.crash_count + 1)
    if record.status.is_terminal:
        return record
    kind = event.kind
    if kind is RunEventKind.ALLOCATED:
        return replace(record, allocated_at=event.at)
    if kind is RunEventKind.ASSIGNED:
        worker = data.get("worker_id")
        device = data.get("device_id")
        return replace(
            record,
            status=RunStatus.ASSIGNED if record.status is RunStatus.ALLOCATED else record.status,
            assigned_at=record.assigned_at if record.assigned_at is not None else event.at,
            worker_id=WorkerId(str(worker)) if worker else record.worker_id,
            device_id=DeviceId(str(device)) if device else record.device_id,
        )
    if kind in (RunEventKind.LAUNCHED, RunEventKind.STARTED):
        return replace(
            record,
            status=RunStatus.RUNNING,
            started_at=record.started_at if record.started_at is not None else event.at,
        )
    if kind is RunEventKind.STOP_REQUESTED:
        reason = data.get("reason")
        return replace(record, stop_reason=str(reason) if reason else record.stop_reason)
    if kind is RunEventKind.EXITED:
        outcome = str(data.get("outcome", "crashed"))
        code = data.get("exit_code")
        reason = data.get("reason")
        return replace(
            record,
            status=OUTCOME_STATUS.get(outcome, RunStatus.CRASHED),
            ended_at=event.at,
            exit_code=int(code) if isinstance(code, int) else None,
            stop_reason=str(reason) if reason else record.stop_reason,
        )
    if kind is RunEventKind.LOST:
        reason = data.get("reason")
        return replace(
            record,
            status=RunStatus.LOST,
            ended_at=event.at,
            stop_reason=str(reason) if reason else "supervisor gone",
        )
    return record


class FileRunRegistry:
    """RunRegistry backed by `StateLayout` files."""

    def __init__(self, layout: StateLayout, experiment_id: str, trainer: str, total_steps: int):
        self._layout = layout
        self._experiment_id = experiment_id
        self._trainer = trainer
        self._total_steps = total_steps

    @contextmanager
    def _counters_lock(self) -> Iterator[None]:
        path = self._layout.locks_dir / "counters.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _next_number(self) -> int:
        counters = read_json(self._layout.counters_file) or {}
        from_counter = int(counters.get("next_run", 1))
        from_disk = max(self._numbers_on_disk(), default=0) + 1
        return max(from_counter, from_disk)

    def _numbers_on_disk(self) -> Iterator[int]:
        if not self._layout.runs_dir.exists():
            return
        for child in self._layout.runs_dir.iterdir():
            try:
                yield parse_run_id(child.name)
            except ValueError:
                continue

    def allocate(self, proposal: ProposedRun, round_index: int) -> RunSpec:
        with self._counters_lock():
            number = self._next_number()
            run_id = format_run_id(number)
            spec = RunSpec(
                run_id=run_id,
                experiment_id=self._experiment_id,
                round_index=round_index,
                trainer=self._trainer,
                config=dict(proposal.config),
                seed=proposal.seed,
                total_steps=self._total_steps,
                purpose=proposal.purpose,
                parent_run_id=proposal.parent_run_id,
                rationale=proposal.rationale,
            )
            self._layout.run_dir(run_id).mkdir(parents=True, exist_ok=False)
            write_json_atomic(self._layout.run_spec(run_id), encode(spec))
            counters = dict(read_json(self._layout.counters_file) or {})
            write_json_atomic(self._layout.counters_file, {**counters, "next_run": number + 1})
        self.append(
            RunEvent(
                run_id=run_id,
                at=time.time(),
                kind=RunEventKind.ALLOCATED,
                source=EventSource.MASTER,
                data={"round_index": round_index, "purpose": proposal.purpose.value},
            )
        )
        return spec

    def append(self, event: RunEvent) -> None:
        if not self._layout.run_spec(event.run_id).exists():
            raise RegistryError(f"unknown run {event.run_id}")
        append_line(self._layout.run_events(event.run_id), encode(event))

    def spec(self, run_id: RunId) -> RunSpec | None:
        raw = read_json(self._layout.run_spec(run_id))
        if raw is None:
            return None
        try:
            return decode(RunSpec, raw)
        except CodecError:
            return None

    def events(self, run_id: RunId) -> tuple[RunEvent, ...]:
        result = read_lines(self._layout.run_events(run_id))
        events = []
        for raw in result.records:
            try:
                events.append(decode(RunEvent, raw))
            except CodecError:
                continue
        return tuple(events)

    def get(self, run_id: RunId) -> RunRecord | None:
        spec = self.spec(run_id)
        if spec is None:
            return None
        return fold(spec, self.events(run_id))

    def all(self) -> tuple[RunRecord, ...]:
        numbers = sorted(self._numbers_on_disk())
        records = (self.get(format_run_id(n)) for n in numbers)
        return tuple(r for r in records if r is not None)


def load_registry(layout: StateLayout) -> FileRunRegistry:
    """Registry for read-only consumers (dashboard, workers) using experiment.json."""
    experiment = read_json(layout.experiment_file) or {}
    trainer = experiment.get("trainer", {}) if isinstance(experiment, dict) else {}
    return FileRunRegistry(
        layout,
        experiment_id=str(experiment.get("experiment_id", "unknown")),
        trainer=str(trainer.get("name", "unknown")),
        total_steps=int(trainer.get("total_steps", 0)),
    )
