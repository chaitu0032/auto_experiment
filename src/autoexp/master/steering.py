"""Steering: master -> worker messages, firstmate style.

`send()` posts a durable inbox message, then nudges the worker's pane with one
line and Enter, but only when the worker is not in takeover and Herdr reports
its agent idle; otherwise the nudge is held (a `steer_held` event) and retried
next tick. The message itself is already durable, so holding never loses it.
`read_context()` returns the pane tail for observation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.core.ids import WorkerId
from autoexp.core.models import InboxKind, InboxMessage, JsonValue, WorkerRecord
from autoexp.herdr.adapter import HerdrAdapter
from autoexp.state.inbox import Inbox


@dataclass(frozen=True, slots=True)
class SteerResult:
    message: InboxMessage | None  # None when the msg_id already existed
    nudged: bool
    held_reason: str | None


class Steering:
    def __init__(self, inbox: Inbox, herdr: HerdrAdapter) -> None:
        self._inbox = inbox
        self._herdr = herdr

    def send(
        self, worker: WorkerRecord, msg_id: str, kind: InboxKind, body: Mapping[str, JsonValue]
    ) -> SteerResult:
        raise NotImplementedError

    def nudge_pending(self, worker: WorkerRecord) -> SteerResult:
        """Re-nudge a worker that has unhandled messages (held earlier)."""
        raise NotImplementedError

    def read_context(self, worker: WorkerRecord, lines: int) -> str:
        raise NotImplementedError

    def can_type(self, worker: WorkerRecord) -> tuple[bool, str | None]:
        """(allowed, reason held). Human takeover and busy panes win."""
        raise NotImplementedError


def nudge_text(worker_id: WorkerId, pending: int) -> str:
    return (
        f"autoexp: {pending} message(s) waiting for {worker_id}; "
        f"run `autoexp worker handle --worker {worker_id}`"
    )
