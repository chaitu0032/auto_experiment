"""Durable per-worker steering inbox (master -> worker).

Messages are JSON files `workers/<id>/inbox/<seq>-<msg_id>.json`, written
atomically, processed in `seq` order, acknowledged by moving them into
`inbox/handled/`. `msg_id` is deterministic (`r0003-assign`), so `post()` is
idempotent: posting a message whose id is pending or handled is a no-op. That
is what lets the master replay intents after a crash.
"""

from __future__ import annotations

import fcntl
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from autoexp.core.ids import WorkerId
from autoexp.core.models import InboxKind, InboxMessage, JsonValue
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import read_json, write_json_atomic
from autoexp.state.layout import StateLayout

MSG_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


class Inbox(Protocol):
    def post(
        self, worker_id: WorkerId, msg_id: str, kind: InboxKind, body: Mapping[str, JsonValue]
    ) -> InboxMessage | None:
        """Write a message; return None if `msg_id` already exists."""
        ...

    def pending(self, worker_id: WorkerId) -> tuple[InboxMessage, ...]: ...

    def ack(self, message: InboxMessage) -> None: ...

    def is_handled(self, worker_id: WorkerId, msg_id: str) -> bool: ...

    def exists(self, worker_id: WorkerId, msg_id: str) -> bool: ...


def _file_name(message: InboxMessage) -> str:
    return f"{message.seq:06d}-{message.msg_id}.json"


class FileInbox:
    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout

    def _find(self, directory: Path, msg_id: str) -> Path | None:
        if not directory.exists():
            return None
        matches = sorted(directory.glob(f"*-{msg_id}.json"))
        exact = [m for m in matches if m.name.split("-", 1)[1] == f"{msg_id}.json"]
        return exact[0] if exact else None

    def exists(self, worker_id: WorkerId, msg_id: str) -> bool:
        inbox = self._layout.worker_inbox(worker_id)
        handled = self._layout.worker_inbox_handled(worker_id)
        return self._find(inbox, msg_id) is not None or self._find(handled, msg_id) is not None

    def is_handled(self, worker_id: WorkerId, msg_id: str) -> bool:
        return self._find(self._layout.worker_inbox_handled(worker_id), msg_id) is not None

    def post(
        self, worker_id: WorkerId, msg_id: str, kind: InboxKind, body: Mapping[str, JsonValue]
    ) -> InboxMessage | None:
        if MSG_ID_PATTERN.match(msg_id) is None:
            raise ValueError(f"invalid message id: {msg_id!r}")
        inbox = self._layout.worker_inbox(worker_id)
        inbox.mkdir(parents=True, exist_ok=True)
        lock_path = inbox / ".lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.exists(worker_id, msg_id):
                return None
            seq_file = inbox / ".seq.json"
            seq = int((read_json(seq_file) or {}).get("next", 1))
            message = InboxMessage(
                msg_id=msg_id,
                seq=seq,
                worker_id=worker_id,
                at=time.time(),
                kind=kind,
                body=dict(body),
            )
            write_json_atomic(inbox / _file_name(message), encode(message))
            write_json_atomic(seq_file, {"next": seq + 1})
            return message
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def pending(self, worker_id: WorkerId) -> tuple[InboxMessage, ...]:
        inbox = self._layout.worker_inbox(worker_id)
        if not inbox.exists():
            return ()
        messages = []
        for path in sorted(inbox.glob("[0-9]*-*.json")):
            raw = read_json(path)
            if raw is None:
                continue
            try:
                messages.append(decode(InboxMessage, raw))
            except CodecError:
                continue
        return tuple(sorted(messages, key=lambda m: m.seq))

    def ack(self, message: InboxMessage) -> None:
        source = self._layout.worker_inbox(message.worker_id) / _file_name(message)
        handled = self._layout.worker_inbox_handled(message.worker_id)
        handled.mkdir(parents=True, exist_ok=True)
        if source.exists():
            os.replace(source, handled / source.name)
