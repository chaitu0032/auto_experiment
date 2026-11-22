"""flock locks: exclusivity across processes, probe, release on death."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from autoexp.core.models import LockKind
from autoexp.state.locks import FlockLockManager, LockHeld

HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from autoexp.core.models import LockKind
    from autoexp.state.locks import FlockLockManager
    FlockLockManager().acquire(Path(sys.argv[1]), LockKind.RUN, "holder")
    print("held", flush=True)
    time.sleep(60)
    """
)


def _hold(path: Path) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_acquire_release_and_sidecar(tmp_path: Path) -> None:
    locks = FlockLockManager()
    path = tmp_path / "locks" / "master.lock"
    held = locks.acquire(path, LockKind.MASTER, "master")
    holder = locks.holder(path)
    assert holder is not None and holder.owner == "master" and holder.kind is LockKind.MASTER
    locks.release(held)
    assert locks.holder(path) is None
    assert not locks.probe(path)
    locks.release(locks.acquire(path, LockKind.MASTER, "again"))


def test_second_process_cannot_take_a_held_lock(tmp_path: Path) -> None:
    path = tmp_path / "run-r0001.lock"
    proc = _hold(path)
    try:
        locks = FlockLockManager()
        assert locks.probe(path)
        with pytest.raises(LockHeld) as err:
            locks.acquire(path, LockKind.RUN, "intruder")
        assert err.value.holder is not None and err.value.holder.owner == "holder"
        assert "held by pid" in str(err.value)
    finally:
        proc.kill()
        proc.wait()
    deadline = time.time() + 5
    while FlockLockManager().probe(path) and time.time() < deadline:
        time.sleep(0.05)
    assert not FlockLockManager().probe(path), "lock must be released when the holder dies"
    FlockLockManager().acquire(path, LockKind.RUN, "after-death")


def test_probe_missing_lock_is_false(tmp_path: Path) -> None:
    assert not FlockLockManager().probe(tmp_path / "none.lock")
