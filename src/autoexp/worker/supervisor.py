"""Run supervisor (`autoexp run-exec <run_id>`): one per run, detached.

Holds the run lock and its device lock for its whole life (so a run is never
driven twice and a device never runs two), spawns the trainer command from the
spec with `--threads` set from the device slot, tees stdout/stderr to
stdout.log, writes launched/started/heartbeat/exited run events, polls
control.json for stop requests (SIGTERM, then SIGKILL after a grace period),
and classifies the exit as completed, solved, pruned, stopped or crashed.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.core.ids import RunId
from autoexp.core.models import RunStatus
from autoexp.state.layout import StateLayout
from autoexp.state.locks import LockManager


@dataclass(frozen=True, slots=True)
class SupervisorOutcome:
    run_id: RunId
    status: RunStatus
    exit_code: int | None
    reason: str


def supervise(layout: StateLayout, locks: LockManager, run_id: RunId) -> SupervisorOutcome:
    raise NotImplementedError
