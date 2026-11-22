"""Run registry: allocation, folding events into records, persistence."""

from __future__ import annotations

import pytest

from autoexp.core.ids import RunId
from autoexp.core.models import (
    EventSource,
    ProposedRun,
    RunEvent,
    RunEventKind,
    RunPurpose,
    RunStatus,
)
from autoexp.state.layout import StateLayout
from autoexp.state.registry import FileRunRegistry, RegistryError, fold
from tests.conftest import spec


def _registry(layout: StateLayout) -> FileRunRegistry:
    return FileRunRegistry(layout, "test", "fake", 1000)


def _ev(run: str, kind: RunEventKind, at: float = 1.0, **data: object) -> RunEvent:
    return RunEvent(RunId(run), at, kind, EventSource.SUPERVISOR, data)  # type: ignore[arg-type]


def test_allocate_sequential_ids_survive_restart(layout: StateLayout) -> None:
    reg = _registry(layout)
    a = reg.allocate(ProposedRun({"lr": 0.1}, 1, RunPurpose.EXPLORE), 1)
    b = reg.allocate(ProposedRun({"lr": 0.2}, 1, RunPurpose.EXPLORE), 1)
    assert (a.run_id, b.run_id) == ("r0001", "r0002")
    c = _registry(layout).allocate(ProposedRun({"lr": 0.3}, 2, RunPurpose.SEED_CHECK), 2)
    assert c.run_id == "r0003"
    record = reg.get(c.run_id)
    assert record is not None and record.status is RunStatus.ALLOCATED
    assert [r.spec.run_id for r in reg.all()] == ["r0001", "r0002", "r0003"]


def test_counter_is_not_reused_if_counters_file_is_lost(layout: StateLayout) -> None:
    reg = _registry(layout)
    reg.allocate(ProposedRun({}, 1, RunPurpose.EXPLORE), 1)
    layout.counters_file.unlink()
    assert reg.allocate(ProposedRun({}, 1, RunPurpose.EXPLORE), 1).run_id == "r0002"


def test_append_unknown_run_fails(layout: StateLayout) -> None:
    with pytest.raises(RegistryError):
        _registry(layout).append(_ev("r0009", RunEventKind.STARTED))


def test_fold_lifecycle() -> None:
    s = spec(1)
    events = [
        _ev("r0001", RunEventKind.ALLOCATED, 1.0),
        _ev("r0001", RunEventKind.ASSIGNED, 2.0, worker_id="w2", device_id="cpu1"),
        _ev("r0001", RunEventKind.LAUNCHED, 3.0),
        _ev("r0001", RunEventKind.STARTED, 4.0),
    ]
    running = fold(s, events)
    assert running.status is RunStatus.RUNNING
    assert (running.worker_id, running.device_id, running.started_at) == ("w2", "cpu1", 3.0)
    done = fold(
        s,
        [
            *events,
            _ev("r0001", RunEventKind.EXITED, 9.0, exit_code=0, outcome="pruned", reason="halving"),
        ],
    )
    assert (
        done.status is RunStatus.PRUNED and done.ended_at == 9.0 and done.stop_reason == "halving"
    )


def test_fold_terminal_ignores_later_events_but_counts_crashes() -> None:
    s = spec(1)
    events = [
        _ev("r0001", RunEventKind.EXITED, 2.0, exit_code=1, outcome="crashed", reason="boom"),
        _ev("r0001", RunEventKind.STARTED, 3.0),
        _ev("r0001", RunEventKind.LOST, 4.0),
    ]
    record = fold(s, events)
    assert record.status is RunStatus.CRASHED and record.crash_count == 1 and record.exit_code == 1


def test_fold_lost_and_unknown_outcome() -> None:
    s = spec(1)
    assert fold(s, [_ev("r0001", RunEventKind.LOST, reason="gone")]).status is RunStatus.LOST
    weird = fold(s, [_ev("r0001", RunEventKind.EXITED, outcome="???")])
    assert weird.status is RunStatus.CRASHED
