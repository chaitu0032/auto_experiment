"""Run supervisor (`autoexp run-exec <run_id>`): one per run, detached.

Holds the run lock and its device lock for its whole life (so a run is never
driven twice and a device never runs two), spawns the trainer command from the
spec with `--threads` set from the device slot, tees stdout/stderr to
stdout.log, writes launched/started/exited run events, polls control.json for
stop requests (SIGTERM, then SIGKILL after a grace period), and classifies the
exit as completed, solved, pruned, stopped or crashed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from autoexp.core.ids import RunId
from autoexp.core.models import EventSource, LockKind, RunEvent, RunEventKind, RunStatus
from autoexp.observe.logs import find_traceback, last_line
from autoexp.state.jsonl import read_json
from autoexp.state.layout import StateLayout
from autoexp.state.locks import HeldLock, LockHeld, LockManager
from autoexp.state.registry import OUTCOME_STATUS, FileRunRegistry, load_registry
from autoexp.trainers.base import build_invocation

POLL_S = 1.0
STOP_GRACE_S = 20.0
STOP_CATEGORIES = ("pruned", "solved", "stopped")


class SupervisorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SupervisorOutcome:
    run_id: RunId
    status: RunStatus
    exit_code: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class StopRequest:
    category: str
    reason: str


def read_stop_request(layout: StateLayout, run_id: RunId) -> StopRequest | None:
    raw = read_json(layout.run_control(run_id))
    if not isinstance(raw, dict) or raw.get("action") != "stop":
        return None
    category = raw.get("category") if raw.get("category") in STOP_CATEGORIES else "stopped"
    return StopRequest(category=str(category), reason=str(raw.get("reason", ""))[:500])


def _trainer_command(layout: StateLayout) -> tuple[tuple[str, ...], int]:
    experiment = read_json(layout.experiment_file)
    if not isinstance(experiment, dict):
        raise SupervisorError("experiment.json missing; the master writes it at start")
    command = tuple(experiment["trainer"]["command"])
    if command and command[0] in ("python", "python3"):
        command = (sys.executable, *command[1:])
    threads = int(experiment["devices"]["threads_per_slot"])
    return command, threads


class _Signals:
    received = False


def _on_signal(signum: int, frame: object) -> None:
    _Signals.received = True


def supervise(layout: StateLayout, locks: LockManager, run_id: RunId) -> SupervisorOutcome:
    registry = load_registry(layout)
    record = registry.get(run_id)
    if record is None:
        raise SupervisorError(f"unknown run {run_id}")
    if record.status.is_terminal:
        raise SupervisorError(f"run {run_id} already ended ({record.status.value})")
    if record.device_id is None or record.worker_id is None:
        raise SupervisorError(f"run {run_id} is not assigned to a worker and device")
    run_lock = locks.acquire(layout.run_lock(run_id), LockKind.RUN, f"supervisor:{run_id}")
    try:
        try:
            device_lock = locks.acquire(
                layout.device_lock(record.device_id), LockKind.DEVICE, f"supervisor:{run_id}"
            )
        except LockHeld as err:
            raise SupervisorError(f"device {record.device_id} busy: {err}") from err
        try:
            return _run(layout, registry, record.device_id, run_id, run_lock, device_lock)
        finally:
            locks.release(device_lock)
    finally:
        locks.release(run_lock)


def _event(registry: FileRunRegistry, run_id: RunId, kind: RunEventKind, **data: object) -> None:
    registry.append(
        RunEvent(run_id=run_id, at=time.time(), kind=kind, source=EventSource.SUPERVISOR, data=data)  # type: ignore[arg-type]
    )


def _run(
    layout: StateLayout,
    registry: FileRunRegistry,
    device_id: str,
    run_id: RunId,
    run_lock: HeldLock,
    device_lock: HeldLock,
) -> SupervisorOutcome:
    spec = registry.spec(run_id)
    assert spec is not None
    command, threads = _trainer_command(layout)
    invocation = build_invocation(command, spec, str(layout.run_dir(run_id)), device_id, threads)
    _event(registry, run_id, RunEventKind.LAUNCHED, pid=os.getpid(), device_id=device_id)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    stdout_path = layout.run_stdout(run_id)
    with stdout_path.open("ab") as log:
        try:
            proc = subprocess.Popen(
                list(invocation.argv),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=dict(invocation.env),
                cwd=str(layout.run_dir(run_id)),
            )
        except OSError as err:
            _event(
                registry,
                run_id,
                RunEventKind.EXITED,
                exit_code=None,
                outcome="crashed",
                reason=f"could not start trainer: {err}",
            )
            return SupervisorOutcome(run_id, RunStatus.CRASHED, None, str(err))
        _event(
            registry,
            run_id,
            RunEventKind.STARTED,
            trainer_pid=proc.pid,
            argv=list(invocation.argv[:3]),
        )
        stop: StopRequest | None = None
        term_sent_at: float | None = None
        while proc.poll() is None:
            if stop is None:
                stop = read_stop_request(layout, run_id)
                if stop is None and _Signals.received:
                    stop = StopRequest("stopped", "supervisor signalled")
                if stop is not None:
                    _event(
                        registry,
                        run_id,
                        RunEventKind.STOP_REQUESTED,
                        reason=stop.reason,
                        category=stop.category,
                    )
                    proc.terminate()
                    term_sent_at = time.monotonic()
            elif term_sent_at is not None and time.monotonic() - term_sent_at > STOP_GRACE_S:
                proc.kill()
                term_sent_at = None
            time.sleep(POLL_S)
    code = proc.returncode
    outcome, reason = classify(code, stop, stdout_path)
    _event(registry, run_id, RunEventKind.EXITED, exit_code=code, outcome=outcome, reason=reason)
    return SupervisorOutcome(run_id, OUTCOME_STATUS[outcome], code, reason)


def classify(code: int | None, stop: StopRequest | None, stdout_path: Path) -> tuple[str, str]:
    """Exit outcome and reason from the exit code, any stop request and the log."""
    if stop is not None:
        return stop.category, stop.reason or stop.category
    if code == 0:
        return "completed", "trainer finished its step budget"
    text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    trace = find_traceback(text)
    detail = last_line(trace) if trace else last_line(text)
    return "crashed", f"exit code {code}: {detail}"[:500]
