"""flock-based locks: one master, one driver per run, one run per device.

A lock is an open file descriptor holding `fcntl.flock(LOCK_EX | LOCK_NB)` plus
a `LockInfo` JSON sidecar for display. The kernel releases it when the holder
dies, so `probe()` (try, then release at once) tells a live holder from a dead
one without trusting PIDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autoexp.core.models import LockInfo, LockKind


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
    def acquire(self, path: Path, kind: LockKind, owner: str) -> HeldLock:
        raise NotImplementedError

    def release(self, lock: HeldLock) -> None:
        raise NotImplementedError

    def probe(self, path: Path) -> bool:
        raise NotImplementedError

    def holder(self, path: Path) -> LockInfo | None:
        raise NotImplementedError
