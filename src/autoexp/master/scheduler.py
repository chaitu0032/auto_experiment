"""Place accepted proposals on workers and devices under the configured caps.

Pure planning: given worker records, live runs and free device slots (probed
via device locks), return Assignments; the loop writes them as intents.
Caps: runs_per_worker, max_concurrent_runs, one run per device.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.config.schema import DevicesConfig, WorkersConfig
from autoexp.core.ids import DeviceId, RunId, WorkerId
from autoexp.core.models import RunRecord, WorkerRecord


@dataclass(frozen=True, slots=True)
class Assignment:
    run_id: RunId
    worker_id: WorkerId
    device_id: DeviceId


class Scheduler:
    def __init__(self, workers: WorkersConfig, devices: DevicesConfig) -> None:
        self._workers = workers
        self._devices = devices

    def free_slots(self, live: tuple[RunRecord, ...], free_devices: tuple[DeviceId, ...]) -> int:
        raise NotImplementedError

    def assign(
        self,
        pending: tuple[RunRecord, ...],
        workers: tuple[WorkerRecord, ...],
        live: tuple[RunRecord, ...],
        free_devices: tuple[DeviceId, ...],
    ) -> tuple[Assignment, ...]:
        raise NotImplementedError
