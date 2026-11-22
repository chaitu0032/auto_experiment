"""Place accepted proposals on workers and devices under the configured caps.

Pure planning: given worker records, live runs and free device slots (probed
via device locks), return Assignments; the loop writes them as intents.
Caps: runs_per_worker, max_concurrent_runs, one run per device.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.config.schema import DevicesConfig, WorkersConfig
from autoexp.core.ids import DeviceId, RunId, WorkerId
from autoexp.core.models import RunRecord, RunStatus, WorkerRecord

LIVE = (RunStatus.ASSIGNED, RunStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class Assignment:
    run_id: RunId
    worker_id: WorkerId
    device_id: DeviceId


class Scheduler:
    def __init__(self, workers: WorkersConfig, devices: DevicesConfig) -> None:
        self._workers = workers
        self._devices = devices

    def capacity(self) -> int:
        return min(
            self._devices.max_concurrent_runs,
            self._workers.count * self._workers.runs_per_worker,
            len(self._devices.slots),
        )

    def free_slots(self, live: tuple[RunRecord, ...], free_devices: tuple[DeviceId, ...]) -> int:
        """Runs that could start now: capacity minus live runs, bounded by free devices."""
        busy = sum(1 for r in live if r.status in LIVE)
        return max(0, min(self.capacity() - busy, len(free_devices)))

    def assign(
        self,
        pending: tuple[RunRecord, ...],
        workers: tuple[WorkerRecord, ...],
        live: tuple[RunRecord, ...],
        free_devices: tuple[DeviceId, ...],
    ) -> tuple[Assignment, ...]:
        """Greedy: least-loaded worker without takeover, lowest free device."""
        load = {w.worker_id: 0 for w in workers if not w.takeover}
        for record in live:
            if record.status in LIVE and record.worker_id in load:
                load[record.worker_id] += 1
        used_devices = {r.device_id for r in live if r.status in LIVE}
        devices = [d for d in free_devices if d not in used_devices]
        busy = sum(1 for r in live if r.status in LIVE)
        result = []
        for record in sorted(pending, key=lambda r: r.spec.run_id):
            if busy >= self.capacity() or not devices:
                break
            candidates = [w for w, n in load.items() if n < self._workers.runs_per_worker]
            if not candidates:
                break
            worker = min(candidates, key=lambda w: (load[w], w))
            device = devices.pop(0)
            result.append(Assignment(record.spec.run_id, worker, device))
            load[worker] += 1
            busy += 1
        return tuple(result)
