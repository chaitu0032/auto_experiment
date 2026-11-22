"""`autoexp worker serve`: the ShellRuntime loop (deterministic worker).

Every second it runs the mechanical inbox handler and prints what changed for
its runs. Anything a human types into the pane wins: a typed line puts the
worker in takeover (the inbox is left untouched, so master messages queue up)
until the human types `release`. Commands: `status`, `stop <run_id>`,
`release`, `help`. DEBUG requests are answered with verdict `unfixable` (no
agent to debug); approved code changes are declined (no agent to apply them).
"""

from __future__ import annotations

import select
import sys
import time
from collections.abc import Callable

from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import InboxKind, RunStatus, WorkerState
from autoexp.state.inbox import FileInbox
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout
from autoexp.state.registry import load_registry
from autoexp.worker.handler import handle_inbox
from autoexp.worker.state import has_takeover, report, set_takeover

HELP = "commands: status | stop <run_id> | release | help  (any input = human takeover)"


def _say(text: str) -> None:
    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
    sys.stdout.flush()


def _read_line(timeout: float) -> str | None:
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (ValueError, OSError):
        time.sleep(timeout)
        return None
    if not ready:
        return None
    line = sys.stdin.readline()
    return line.strip() if line else None


def human_command(layout: StateLayout, worker_id: WorkerId, line: str) -> str:
    """Apply one typed line; returns what to print. Pure file effects only."""
    words = line.split()
    if not words:
        return HELP
    verb = words[0].lower()
    if verb == "release":
        set_takeover(layout, worker_id, False, "human released the pane")
        return "released: the master steers this worker again"
    if not has_takeover(layout, worker_id):
        set_takeover(layout, worker_id, True, f"human typed: {line[:80]}")
    if verb == "help":
        return HELP
    if verb == "status":
        runs = [r for r in load_registry(layout).all() if r.worker_id == worker_id]
        return "; ".join(f"{r.spec.run_id} {r.status.value}" for r in runs) or "no runs"
    if verb == "stop" and len(words) == 2:
        run_id = RunId(words[1])
        record = load_registry(layout).get(run_id)
        if record is None or record.worker_id != worker_id:
            return f"{run_id} is not one of this worker's runs"
        write_json_atomic(
            layout.run_control(run_id),
            {
                "action": "stop",
                "category": "stopped",
                "reason": "stopped by the human",
                "by": "human",
                "at": time.time(),
            },
        )
        return f"{run_id}: stop requested by the human"
    return f"takeover on (unrecognised: {line!r}); {HELP}"


def serve(
    layout: StateLayout,
    worker_id: WorkerId,
    *,
    poll_s: float = 1.0,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    inbox = FileInbox(layout)
    seen: dict[str, RunStatus] = {}
    report(layout, worker_id, WorkerState.IDLE, note="shell worker started")
    _say(f"worker {worker_id} (shell runtime) serving {layout.root}")
    _say(HELP)
    while should_continue():
        line = _read_line(poll_s)
        if line is not None:
            _say(human_command(layout, worker_id, line))
        if not has_takeover(layout, worker_id):
            result = handle_inbox(layout, inbox, worker_id)
            for text in result.lines:
                _say(text)
            for message in result.needs_agent:
                run_id = (
                    RunId(str(message.body.get("run_id", "")))
                    if message.body.get("run_id")
                    else None
                )
                if message.kind is InboxKind.DEBUG:
                    report(
                        layout,
                        worker_id,
                        WorkerState.BLOCKED,
                        run_id,
                        f"shell runtime cannot debug: {message.body.get('reason', '')}",
                        "unfixable",
                    )
                else:
                    report(
                        layout,
                        worker_id,
                        WorkerState.IDLE,
                        run_id,
                        "shell runtime cannot apply code changes; declined",
                    )
        for record in load_registry(layout).all():
            if record.worker_id != worker_id:
                continue
            if seen.get(record.spec.run_id) is not record.status:
                seen[record.spec.run_id] = record.status
                reason = f" ({record.stop_reason})" if record.stop_reason else ""
                _say(f"{record.spec.run_id}: {record.status.value}{reason}")
