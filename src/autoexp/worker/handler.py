"""`autoexp worker handle`: process a worker's inbox once, mechanically.

ASSIGN -> spawn a detached run supervisor (`autoexp run-exec <run_id>`);
STOP -> write runs/<id>/control.json; DEBUG and APPROVAL_RESULT -> returned
for the agent (the shell runtime reports them as unfixable/declined). Each
handled message is acked by moving it to handled/.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass

from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import InboxKind, InboxMessage, WorkerState
from autoexp.state.inbox import Inbox
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout
from autoexp.state.locks import FlockLockManager
from autoexp.state.registry import load_registry
from autoexp.worker.state import report


@dataclass(frozen=True, slots=True)
class HandleResult:
    handled: tuple[InboxMessage, ...]
    needs_agent: tuple[InboxMessage, ...]  # DEBUG / APPROVAL_RESULT
    errors: tuple[str, ...]
    lines: tuple[str, ...]  # human-readable digest of what was done


def spawn_supervisor(layout: StateLayout, run_id: RunId) -> int:
    log_path = layout.run_dir(run_id) / "supervisor.log"
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "autoexp", "--state-dir", str(layout.root), "run-exec", run_id],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return proc.pid


def _assign(layout: StateLayout, worker_id: WorkerId, message: InboxMessage) -> str:
    run_id = RunId(str(message.body.get("run_id", "")))
    record = load_registry(layout).get(run_id)
    if record is None:
        raise ValueError(f"assign: unknown run {run_id}")
    if record.worker_id != worker_id:
        raise ValueError(f"assign: {run_id} belongs to {record.worker_id}, not {worker_id}")
    if record.status.is_terminal:
        return f"{run_id}: already {record.status.value}, nothing to launch"
    if FlockLockManager().probe(layout.run_lock(run_id)):
        return f"{run_id}: already running"
    pid = spawn_supervisor(layout, run_id)
    report(layout, worker_id, WorkerState.WORKING, run_id, f"launched supervisor pid {pid}")
    return f"{run_id}: launched on {record.device_id} (supervisor pid {pid})"


def _stop(layout: StateLayout, worker_id: WorkerId, message: InboxMessage) -> str:
    run_id = RunId(str(message.body.get("run_id", "")))
    record = load_registry(layout).get(run_id)
    if record is None:
        raise ValueError(f"stop: unknown run {run_id}")
    if record.status.is_terminal:
        return f"{run_id}: already {record.status.value}"
    write_json_atomic(
        layout.run_control(run_id),
        {
            "action": "stop",
            "category": str(message.body.get("category", "stopped")),
            "reason": str(message.body.get("reason", "")),
            "by": worker_id,
            "at": time.time(),
        },
    )
    category = message.body.get("category", "stopped")
    return f"{run_id}: stop requested ({category}: {message.body.get('reason', '')})"


def handle_inbox(layout: StateLayout, inbox: Inbox, worker_id: WorkerId) -> HandleResult:
    handled, needs_agent, errors, lines = [], [], [], []
    for message in inbox.pending(worker_id):
        try:
            if message.kind is InboxKind.ASSIGN:
                lines.append(_assign(layout, worker_id, message))
            elif message.kind is InboxKind.STOP:
                lines.append(_stop(layout, worker_id, message))
            elif message.kind in (InboxKind.DEBUG, InboxKind.APPROVAL_RESULT):
                needs_agent.append(message)
                lines.append(
                    f"{message.msg_id}: {message.kind.value} needs the agent: {dict(message.body)}"
                )
            else:
                lines.append(f"{message.msg_id}: note: {message.body.get('text', '')}")
        except (ValueError, OSError) as err:
            errors.append(f"{message.msg_id}: {err}")
            lines.append(f"{message.msg_id}: ERROR {err}")
        inbox.ack(message)
        handled.append(message)
    return HandleResult(tuple(handled), tuple(needs_agent), tuple(errors), tuple(lines))
