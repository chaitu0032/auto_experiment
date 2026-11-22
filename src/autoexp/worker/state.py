"""Worker-side durable records: worker.json, events.jsonl, takeover marker."""

from __future__ import annotations

import time
from dataclasses import replace

from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import WorkerEvent, WorkerRecord, WorkerState
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import append_line, read_json, read_lines, write_json_atomic
from autoexp.state.layout import StateLayout


def report(
    layout: StateLayout,
    worker_id: WorkerId,
    state: WorkerState,
    run_id: RunId | None = None,
    note: str = "",
    verdict: str | None = None,
) -> WorkerEvent:
    event = WorkerEvent(
        worker_id=worker_id,
        at=time.time(),
        state=state,
        run_id=run_id,
        note=note[:1000],
        verdict=verdict,
    )
    append_line(layout.worker_events(worker_id), encode(event))
    return event


def worker_events(
    layout: StateLayout, worker_id: WorkerId, offset: int = 0
) -> tuple[tuple[WorkerEvent, ...], int]:
    result = read_lines(layout.worker_events(worker_id), offset)
    events = []
    for raw in result.records:
        try:
            events.append(decode(WorkerEvent, raw))
        except CodecError:
            continue
    return tuple(events), result.next_offset


def set_takeover(layout: StateLayout, worker_id: WorkerId, on: bool, note: str = "") -> None:
    marker = layout.worker_takeover(worker_id)
    if on:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{time.time()} {note}\n", encoding="utf-8")
        report(layout, worker_id, WorkerState.TAKEOVER, note=note or "human took over")
    else:
        marker.unlink(missing_ok=True)
        report(layout, worker_id, WorkerState.IDLE, note=note or "released to the master")


def has_takeover(layout: StateLayout, worker_id: WorkerId) -> bool:
    return layout.worker_takeover(worker_id).exists()


def write_worker(layout: StateLayout, record: WorkerRecord) -> None:
    write_json_atomic(layout.worker_file(record.worker_id), encode(record))


def read_worker(layout: StateLayout, worker_id: WorkerId) -> WorkerRecord | None:
    raw = read_json(layout.worker_file(worker_id))
    if raw is None:
        return None
    try:
        record = decode(WorkerRecord, raw)
    except CodecError:
        return None
    events, _ = worker_events(layout, worker_id)
    state = events[-1].state if events else record.state
    return replace(record, state=state, takeover=has_takeover(layout, worker_id))


def list_workers(layout: StateLayout) -> tuple[WorkerRecord, ...]:
    if not layout.workers_dir.exists():
        return ()
    records = (
        read_worker(layout, WorkerId(p.name))
        for p in sorted(layout.workers_dir.iterdir())
        if p.is_dir()
    )
    return tuple(r for r in records if r is not None)
