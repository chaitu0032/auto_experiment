"""Crash recovery: rebuild the master's view from disk before the first tick.

Steps (docs/architecture.md 5): fold all run events; probe run locks of
running runs (held -> re-adopt, free -> mark LOST); replay assign messages for
assigned runs whose message is missing. Decisions whose allocations were cut
short are completed by the loop itself (it re-reads the last decision event).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import EventSource, InboxKind, RunEvent, RunEventKind, RunStatus
from autoexp.state.inbox import Inbox
from autoexp.state.layout import StateLayout
from autoexp.state.locks import LockManager
from autoexp.state.registry import RunRegistry


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    adopted: tuple[RunId, ...]  # live supervisors found holding their run lock
    lost: tuple[RunId, ...]  # running on disk, lock free
    replayed_intents: tuple[str, ...]  # msg ids re-posted
    workers_to_reopen: tuple[WorkerId, ...]
    round_index: int

    @property
    def empty(self) -> bool:
        return not (self.adopted or self.lost or self.replayed_intents)


def assign_msg_id(run_id: RunId) -> str:
    return f"{run_id}-assign"


def mark_lost(registry: RunRegistry, run_id: RunId, reason: str) -> None:
    registry.append(
        RunEvent(
            run_id=run_id,
            at=time.time(),
            kind=RunEventKind.LOST,
            source=EventSource.MASTER,
            data={"reason": reason},
        )
    )


def recover(
    layout: StateLayout, registry: RunRegistry, locks: LockManager, inbox: Inbox
) -> RecoveryReport:
    adopted, lost, replayed, reopen = [], [], [], set()
    round_index = 0
    for record in registry.all():
        rid = record.spec.run_id
        round_index = max(round_index, record.spec.round_index)
        if record.status is RunStatus.RUNNING:
            if locks.probe(layout.run_lock(rid)):
                adopted.append(rid)
                if record.worker_id:
                    reopen.add(record.worker_id)
                continue
            current = registry.get(rid)  # the supervisor may have just finished
            if current is not None and current.status is RunStatus.RUNNING:
                mark_lost(registry, rid, "run supervisor gone while the master was down")
                lost.append(rid)
        elif record.status is RunStatus.ASSIGNED and record.worker_id:
            reopen.add(record.worker_id)
            msg_id = assign_msg_id(rid)
            if not inbox.exists(record.worker_id, msg_id):
                inbox.post(
                    record.worker_id,
                    msg_id,
                    InboxKind.ASSIGN,
                    {"run_id": rid, "device_id": record.device_id},
                )
                replayed.append(msg_id)
    return RecoveryReport(
        adopted=tuple(adopted),
        lost=tuple(lost),
        replayed_intents=tuple(replayed),
        workers_to_reopen=tuple(sorted(reopen)),
        round_index=round_index,
    )
