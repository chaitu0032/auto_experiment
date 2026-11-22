"""The observability agent (deterministic).

Per tick it builds one `Observation` per run: the folded RunRecord, an updated
CurveSummary, a traceback found in stdout.log (crashed runs), and an excerpt of
the owning worker's pane. It also caches each worker's pane tail in
workers/<id>/pane.json for the dashboard. It reads; it never acts.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import CurveSummary, Observation, RunRecord, RunStatus, WorkerRecord
from autoexp.observe.logs import find_traceback, read_tail
from autoexp.state.codec import encode
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout
from autoexp.telemetry.reader import TelemetryReader
from autoexp.telemetry.summary import summarize

PANE_LINES = 80


@dataclass(frozen=True, slots=True)
class TelemetryCache:
    """Read offsets and summaries per run; a new value every tick."""

    offsets: Mapping[RunId, int] = field(default_factory=dict)
    summaries: Mapping[RunId, CurveSummary] = field(default_factory=dict)
    drained: frozenset[RunId] = frozenset()  # terminal runs read to the end


class Observer:
    def __init__(
        self, layout: StateLayout, telemetry: TelemetryReader, window: int, kl_max: float
    ) -> None:
        self._layout = layout
        self._telemetry = telemetry
        self._window = window
        self._kl_max = kl_max

    def update_telemetry(
        self, records: tuple[RunRecord, ...], cache: TelemetryCache
    ) -> TelemetryCache:
        offsets = dict(cache.offsets)
        summaries = dict(cache.summaries)
        drained = set(cache.drained)
        for record in records:
            rid = record.spec.run_id
            if rid in drained or record.status in (RunStatus.ALLOCATED, RunStatus.ASSIGNED):
                continue
            batch = self._telemetry.read_new(rid, offsets.get(rid, 0))
            if batch.events or batch.malformed or rid not in summaries:
                summaries[rid] = summarize(
                    rid,
                    batch.events,
                    window=self._window,
                    kl_max=self._kl_max,
                    previous=summaries.get(rid),
                    malformed=batch.malformed,
                )
            offsets[rid] = batch.next_offset
            if record.status.is_terminal and not batch.events:
                drained.add(rid)
        return TelemetryCache(offsets=offsets, summaries=summaries, drained=frozenset(drained))

    def observe(
        self,
        records: tuple[RunRecord, ...],
        summaries: Mapping[RunId, CurveSummary],
        pane_tails: Mapping[WorkerId, str],
    ) -> tuple[Observation, ...]:
        observations = []
        for record in records:
            trace = None
            if record.status is RunStatus.CRASHED:
                trace = find_traceback(read_tail(self._layout.run_stdout(record.spec.run_id)))
            excerpt = pane_tails.get(record.worker_id) if record.worker_id else None
            observations.append(
                Observation(
                    record=record,
                    summary=summaries.get(record.spec.run_id),
                    traceback=trace,
                    pane_excerpt=excerpt[-2000:] if excerpt else None,
                )
            )
        return tuple(observations)

    def cache_panes(
        self, workers: tuple[WorkerRecord, ...], read: PaneReader
    ) -> dict[WorkerId, str]:
        tails: dict[WorkerId, str] = {}
        for worker in workers:
            text = read(worker, PANE_LINES)
            tails[worker.worker_id] = text
            write_json_atomic(
                self._layout.worker_pane_tail(worker.worker_id),
                {
                    "worker_id": worker.worker_id,
                    "pane_id": worker.pane_id,
                    "text": text,
                    "read_at": time.time(),
                },
            )
        return tails

    def write_summaries(self, summaries: Mapping[RunId, CurveSummary]) -> None:
        compact = {
            rid: {k: v for k, v in encode(s).items() if k not in ("curve", "recent_returns")}
            for rid, s in summaries.items()
        }
        write_json_atomic(self._layout.master_summaries, compact)


class PaneReader:  # pragma: no cover - typing helper
    def __call__(self, worker: WorkerRecord, lines: int) -> str: ...
