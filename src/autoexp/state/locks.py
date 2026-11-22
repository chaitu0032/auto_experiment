"""flock-based locks: one master, one driver per run, one run per device.

A lock is an open file descriptor holding `fcntl.flock(LOCK_EX | LOCK_NB)` plus
a `LockInfo` JSON sidecar for display. The kernel releases it when the holder
dies, so `probe()` (try, then release at once) tells a live holder from a dead
one without trusting PIDs.
"""

from __future__ import annotations

import fcntl
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autoexp.core.models import LockInfo, LockKind
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import read_json, write_json_atomic
from autoexp.state.layout import StateLayout


class LockHeld(RuntimeError):
    """Raised when a lock is held by someone else; carries the holder's info."""

    def __init__(self, path: Path, holder: LockInfo | None) -> None:
        self.path = path
        self.holder = holder
        who = f"pid {holder.holder_pid} ({holder.owner})" if holder else "unknown holder"
        super().__init__(f"lock {path.name} is held by {who}")


@dataclass(frozen=True, slots=True)
class HeldLock:
    path: Path
    info: LockInfo
    fd: int


class LockManager(Protocol):
    def acquire(self, path: Path, kind: LockKind, owner: str) -> HeldLock:
        """Non-blocking acquire; raise LockHeld if another process holds it."""
        ...

    def release(self, lock: HeldLock) -> None: ...

    def probe(self, path: Path) -> bool:
        """True when some live process holds the lock right now."""
        ...

    def holder(self, path: Path) -> LockInfo | None:
        """Sidecar info of the last holder (may be stale if probe() is False)."""
        ...


class FlockLockManager:
    """Locks are per open file description; do not acquire the same path twice
    in one process (flock would treat separate opens as separate holders)."""

    def acquire(self, path: Path, kind: LockKind, owner: str) -> HeldLock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockHeld(path, self.holder(path)) from None
        except OSError:
            os.close(fd)
            raise
        os.set_inheritable(fd, False)
        info = LockInfo(
            name=path.stem,
            kind=kind,
            holder_pid=os.getpid(),
            host=socket.gethostname(),
            owner=owner,
            acquired_at=time.time(),
        )
        write_json_atomic(StateLayout.lock_info(path), encode(info))
        return HeldLock(path=path, info=info, fd=fd)

    def release(self, lock: HeldLock) -> None:
        StateLayout.lock_info(lock.path).unlink(missing_ok=True)
        try:
            fcntl.flock(lock.fd, fcntl.LOCK_UN)
        finally:
            os.close(lock.fd)

    def probe(self, path: Path) -> bool:
        if not path.exists():
            return False
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def holder(self, path: Path) -> LockInfo | None:
        raw = read_json(StateLayout.lock_info(path))
        if raw is None:
            return None
        try:
            return decode(LockInfo, raw)
        except CodecError:
            return None
