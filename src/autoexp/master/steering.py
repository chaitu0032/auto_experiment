"""Steering: master -> worker messages, firstmate style.

`send()` posts a durable inbox message, then nudges the worker's pane with one
line and Enter, but only when the worker is not in takeover and Herdr reports
its agent idle; otherwise the nudge is held (a `steer_held` event) and retried
next tick. The message itself is already durable, so holding never loses it.
Shell-runtime workers poll their inbox, so they are never typed into: typed
input in their pane always means a human. `read_context()` returns the pane
tail for observation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.core.ids import WorkerId
from autoexp.core.models import InboxKind, InboxMessage, JsonValue, WorkerRecord
from autoexp.herdr.adapter import AgentState, HerdrAdapter
from autoexp.herdr.cli_adapter import HerdrError
from autoexp.state.inbox import Inbox

log = logging.getLogger(__name__)
TYPEABLE_STATES = (AgentState.IDLE, AgentState.UNKNOWN)


@dataclass(frozen=True, slots=True)
class SteerResult:
    message: InboxMessage | None  # None when the msg_id already existed
    nudged: bool
    held_reason: str | None


def nudge_text(worker_id: WorkerId, pending: int) -> str:
    return (
        f"autoexp: {pending} message(s) waiting for {worker_id}; "
        f"run `autoexp worker handle --worker {worker_id}`"
    )


class Steering:
    def __init__(self, inbox: Inbox, herdr: HerdrAdapter, agent_runtime: bool) -> None:
        self._inbox = inbox
        self._herdr = herdr
        self._agent_runtime = agent_runtime

    def send(
        self, worker: WorkerRecord, msg_id: str, kind: InboxKind, body: Mapping[str, JsonValue]
    ) -> SteerResult:
        message = self._inbox.post(worker.worker_id, msg_id, kind, body)
        nudge = self.nudge_pending(worker)
        return SteerResult(message=message, nudged=nudge.nudged, held_reason=nudge.held_reason)

    def nudge_pending(self, worker: WorkerRecord) -> SteerResult:
        """Nudge a worker that has unhandled messages (no-op for shell workers)."""
        if not self._agent_runtime:
            return SteerResult(None, False, None)
        pending = len(self._inbox.pending(worker.worker_id))
        if pending == 0:
            return SteerResult(None, False, None)
        allowed, reason = self.can_type(worker)
        if not allowed:
            return SteerResult(None, False, reason)
        try:
            self.type_line(worker, nudge_text(worker.worker_id, pending))
        except HerdrError as err:
            return SteerResult(None, False, f"herdr: {err}")
        return SteerResult(None, True, None)

    def type_line(self, worker: WorkerRecord, text: str) -> None:
        """Type one line and Enter into the worker's pane (caller checked can_type)."""
        if worker.pane_id is None:
            raise HerdrError(f"{worker.worker_id} has no pane")
        self._herdr.send_text(worker.pane_id, text)
        self._herdr.send_keys(worker.pane_id, "Enter")

    def read_context(self, worker: WorkerRecord, lines: int) -> str:
        if worker.pane_id is None:
            return ""
        try:
            return self._herdr.read(worker.pane_id, lines)
        except HerdrError as err:
            log.warning("cannot read pane of %s: %s", worker.worker_id, err)
            return ""

    def can_type(self, worker: WorkerRecord) -> tuple[bool, str | None]:
        """(allowed, reason held). Human takeover and busy panes win."""
        if worker.takeover:
            return False, "human takeover"
        if worker.pane_id is None:
            return False, "no pane"
        try:
            state = self._herdr.agent_state(worker.pane_id)
        except HerdrError as err:
            return False, f"herdr: {err}"
        if state not in TYPEABLE_STATES:
            return False, f"pane busy ({state.value})"
        return True, None
