"""Durable per-worker steering inbox (master -> worker).

Messages are JSON files `workers/<id>/inbox/<seq>-<msg_id>.json`, written
atomically, processed in `seq` order, acknowledged by moving them into
`inbox/handled/`. `msg_id` is deterministic (`r0003-assign`), so `post()` is
idempotent: posting a message whose id is pending or handled is a no-op. That
is what lets the master replay intents after a crash.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from autoexp.core.ids import WorkerId
from autoexp.core.models import InboxKind, InboxMessage, JsonValue
from autoexp.state.layout import StateLayout


class Inbox(Protocol):
    def post(
        self, worker_id: WorkerId, msg_id: str, kind: InboxKind, body: Mapping[str, JsonValue]
    ) -> InboxMessage | None:
        """Write a message; return None if `msg_id` already exists."""
        ...

    def pending(self, worker_id: WorkerId) -> tuple[InboxMessage, ...]: ...

    def ack(self, message: InboxMessage) -> None: ...

    def is_handled(self, worker_id: WorkerId, msg_id: str) -> bool: ...


class FileInbox:
    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout

    def post(
        self, worker_id: WorkerId, msg_id: str, kind: InboxKind, body: Mapping[str, JsonValue]
    ) -> InboxMessage | None:
        raise NotImplementedError

    def pending(self, worker_id: WorkerId) -> tuple[InboxMessage, ...]:
        raise NotImplementedError

    def ack(self, message: InboxMessage) -> None:
        raise NotImplementedError

    def is_handled(self, worker_id: WorkerId, msg_id: str) -> bool:
        raise NotImplementedError
