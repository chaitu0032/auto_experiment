"""Run supervisor, inbox handler, shell-worker human commands, worker records."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from autoexp.core.ids import DeviceId, RunId, WorkerId
from autoexp.core.models import (
    EventSource,
    InboxKind,
    LockKind,
    ProposedRun,
    RunEvent,
    RunEventKind,
    RunPurpose,
    RunStatus,
    WorkerRecord,
    WorkerState,
)
from autoexp.observe.logs import find_traceback, last_line, read_tail, tail
from autoexp.state.inbox import FileInbox
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout
from autoexp.state.locks import FlockLockManager
from autoexp.state.registry import FileRunRegistry
from autoexp.worker import handler as handler_mod
from autoexp.worker.handler import handle_inbox
from autoexp.worker.serve import human_command
from autoexp.worker.state import has_takeover, list_workers, read_worker, report, write_worker
from autoexp.worker.supervisor import SupervisorError, classify, supervise
from tests.conftest import fake_trainer_command

W1 = WorkerId("w1")


def _experiment(layout: StateLayout, command: list[str]) -> None:
    write_json_atomic(
        layout.experiment_file,
        {
            "experiment_id": "test",
            "started_at": time.time(),
            "trainer": {"name": "fake", "command": command, "total_steps": 3000},
            "devices": {"slots": ["cpu0", "cpu1"], "threads_per_slot": 1},
            "goal": {
                "metric": "rolling_return",
                "window": 10,
                "threshold": 475.0,
                "seeds_required": 3,
            },
        },
    )


def _assigned_run(layout: StateLayout, config: dict, device: str = "cpu0") -> RunId:  # type: ignore[type-arg]
    reg = FileRunRegistry(layout, "test", "fake", 3000)
    spec = reg.allocate(ProposedRun(config, 1, RunPurpose.EXPLORE), 1)
    reg.append(
        RunEvent(
            spec.run_id,
            time.time(),
            RunEventKind.ASSIGNED,
            EventSource.MASTER,
            {"worker_id": "w1", "device_id": device},
        )
    )
    return spec.run_id


def _registry(layout: StateLayout) -> FileRunRegistry:
    return FileRunRegistry(layout, "test", "fake", 3000)


def test_supervisor_completes_and_holds_locks(layout: StateLayout) -> None:
    _experiment(layout, fake_trainer_command())
    rid = _assigned_run(layout, {"cap": 100})
    outcome = supervise(layout, FlockLockManager(), rid)
    assert outcome.status is RunStatus.COMPLETED and outcome.exit_code == 0
    record = _registry(layout).get(rid)
    assert record is not None and record.status is RunStatus.COMPLETED
    kinds = [e.kind for e in _registry(layout).events(rid)]
    assert kinds[-3:] == [RunEventKind.LAUNCHED, RunEventKind.STARTED, RunEventKind.EXITED]
    assert layout.run_metrics(rid).stat().st_size > 0
    assert not FlockLockManager().probe(layout.run_lock(rid))
    with pytest.raises(SupervisorError, match="already ended"):
        supervise(layout, FlockLockManager(), rid)


def test_supervisor_classifies_crash_with_traceback(layout: StateLayout) -> None:
    _experiment(layout, fake_trainer_command())
    rid = _assigned_run(layout, {"crash_at": 1000})
    outcome = supervise(layout, FlockLockManager(), rid)
    assert outcome.status is RunStatus.CRASHED
    assert "fake trainer crash" in outcome.reason


def test_supervisor_stops_on_control_file(layout: StateLayout) -> None:
    _experiment(layout, fake_trainer_command())
    rid = _assigned_run(layout, {"delay": 0.2})
    layout.run_dir(rid).mkdir(parents=True, exist_ok=True)

    def request_stop() -> None:
        time.sleep(1.0)
        write_json_atomic(
            layout.run_control(rid), {"action": "stop", "category": "pruned", "reason": "test"}
        )

    threading.Thread(target=request_stop).start()
    outcome = supervise(layout, FlockLockManager(), rid)
    assert outcome.status is RunStatus.PRUNED and outcome.reason == "test"


def test_supervisor_refuses_busy_device_and_unassigned_runs(layout: StateLayout) -> None:
    _experiment(layout, fake_trainer_command())
    rid = _assigned_run(layout, {})
    locks = FlockLockManager()
    held = locks.acquire(layout.device_lock(DeviceId("cpu0")), LockKind.DEVICE, "other")
    try:
        with pytest.raises(SupervisorError, match="busy"):
            supervise(layout, locks, rid)
    finally:
        locks.release(held)
    reg = _registry(layout)
    lonely = reg.allocate(ProposedRun({}, 1, RunPurpose.EXPLORE), 1)
    with pytest.raises(SupervisorError, match="not assigned"):
        supervise(layout, locks, lonely.run_id)
    with pytest.raises(SupervisorError, match="unknown"):
        supervise(layout, locks, RunId("r0999"))


def test_classify(tmp_path: Path) -> None:
    log = tmp_path / "stdout.log"
    log.write_text("hello\nTraceback (most recent call last):\n  File x\nValueError: bad\nafter\n")
    assert classify(0, None, log) == ("completed", "trainer finished its step budget")
    outcome, reason = classify(1, None, log)
    assert outcome == "crashed" and "ValueError: bad" in reason


def test_log_helpers(tmp_path: Path) -> None:
    text = "a\nTraceback (most recent call last):\n  File y\nKeyError: 1\nz"
    assert find_traceback(text) == "Traceback (most recent call last):\n  File y\nKeyError: 1"
    assert find_traceback("clean") is None
    assert tail("1\n2\n3", 2) == "2\n3" and tail("x", 0) == ""
    assert last_line("a\n\nb\n  \n") == "b" and last_line(None) == ""
    assert read_tail(tmp_path / "missing") == ""


def test_handle_inbox_assign_stop_and_agent_messages(
    layout: StateLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    _experiment(layout, fake_trainer_command())
    rid = _assigned_run(layout, {})
    other = _assigned_run(layout, {}, "cpu1")
    spawned: list[str] = []
    monkeypatch.setattr(
        handler_mod, "spawn_supervisor", lambda lay, run: spawned.append(run) or 4242
    )
    inbox = FileInbox(layout)
    inbox.post(W1, f"{rid}-assign", InboxKind.ASSIGN, {"run_id": rid})
    inbox.post(
        W1,
        f"{other}-stop",
        InboxKind.STOP,
        {"run_id": other, "category": "pruned", "reason": "weak"},
    )
    inbox.post(W1, f"{rid}-debug", InboxKind.DEBUG, {"run_id": rid, "reason": "boom"})
    inbox.post(W1, "bad-assign", InboxKind.ASSIGN, {"run_id": "r0999"})
    result = handle_inbox(layout, inbox, W1)
    assert spawned == [rid]
    assert json.loads(layout.run_control(other).read_text())["category"] == "pruned"
    assert [m.kind for m in result.needs_agent] == [InboxKind.DEBUG]
    assert len(result.errors) == 1 and "unknown run" in result.errors[0]
    assert inbox.pending(W1) == ()


def test_handle_inbox_refuses_someone_elses_run(layout: StateLayout) -> None:
    _experiment(layout, fake_trainer_command())
    rid = _assigned_run(layout, {})
    inbox = FileInbox(layout)
    inbox.post(WorkerId("w2"), f"{rid}-assign", InboxKind.ASSIGN, {"run_id": rid})
    result = handle_inbox(layout, inbox, WorkerId("w2"))
    assert "belongs to w1" in result.errors[0]


def test_human_input_takes_over_the_shell_worker(layout: StateLayout) -> None:
    _experiment(layout, fake_trainer_command())
    rid = _assigned_run(layout, {})
    assert "commands" in human_command(layout, W1, "help")
    assert has_takeover(layout, W1)
    assert rid in human_command(layout, W1, "status")
    assert "stop requested by the human" in human_command(layout, W1, f"stop {rid}")
    assert json.loads(layout.run_control(rid).read_text())["by"] == "human"
    assert "not one of" in human_command(layout, W1, "stop r0999")
    assert "released" in human_command(layout, W1, "release")
    assert not has_takeover(layout, W1)


def test_worker_records(layout: StateLayout) -> None:
    write_worker(layout, WorkerRecord(W1, "w1:p1", "shell", WorkerState.IDLE, False, ()))
    report(layout, W1, WorkerState.BLOCKED, RunId("r0001"), "diagnosis", "unfixable")
    record = read_worker(layout, W1)
    assert record is not None and record.state is WorkerState.BLOCKED and record.pane_id == "w1:p1"
    assert [w.worker_id for w in list_workers(layout)] == ["w1"]
