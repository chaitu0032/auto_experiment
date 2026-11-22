"""On-disk layout of one experiment's state directory, keyed by run ID.

This module is the single owner of path construction (docs/architecture.md 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoexp.core.ids import DeviceId, RunId, WorkerId


@dataclass(frozen=True, slots=True)
class StateLayout:
    root: Path

    # experiment level
    @property
    def experiment_file(self) -> Path:
        return self.root / "experiment.json"

    @property
    def counters_file(self) -> Path:
        return self.root / "counters.json"

    @property
    def stop_flag(self) -> Path:
        return self.root / "STOP"

    @property
    def finding_file(self) -> Path:
        return self.root / "finding.md"

    # master
    @property
    def master_dir(self) -> Path:
        return self.root / "master"

    @property
    def master_events(self) -> Path:
        return self.master_dir / "events.jsonl"

    @property
    def master_heartbeat(self) -> Path:
        return self.master_dir / "heartbeat.json"

    @property
    def telemetry_offsets(self) -> Path:
        return self.master_dir / "offsets.json"

    # locks
    @property
    def locks_dir(self) -> Path:
        return self.root / "locks"

    @property
    def master_lock(self) -> Path:
        return self.locks_dir / "master.lock"

    def run_lock(self, run_id: RunId) -> Path:
        return self.locks_dir / f"run-{run_id}.lock"

    def device_lock(self, device_id: DeviceId) -> Path:
        return self.locks_dir / f"device-{device_id}.lock"

    @staticmethod
    def lock_info(lock_path: Path) -> Path:
        return lock_path.with_suffix(".json")

    # runs
    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def run_dir(self, run_id: RunId) -> Path:
        return self.runs_dir / run_id

    def run_spec(self, run_id: RunId) -> Path:
        return self.run_dir(run_id) / "spec.json"

    def run_events(self, run_id: RunId) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def run_metrics(self, run_id: RunId) -> Path:
        return self.run_dir(run_id) / "metrics.jsonl"

    def run_stdout(self, run_id: RunId) -> Path:
        return self.run_dir(run_id) / "stdout.log"

    def run_control(self, run_id: RunId) -> Path:
        return self.run_dir(run_id) / "control.json"

    def run_checkpoints(self, run_id: RunId) -> Path:
        return self.run_dir(run_id) / "checkpoints"

    # workers
    @property
    def workers_dir(self) -> Path:
        return self.root / "workers"

    def worker_dir(self, worker_id: WorkerId) -> Path:
        return self.workers_dir / worker_id

    def worker_file(self, worker_id: WorkerId) -> Path:
        return self.worker_dir(worker_id) / "worker.json"

    def worker_inbox(self, worker_id: WorkerId) -> Path:
        return self.worker_dir(worker_id) / "inbox"

    def worker_inbox_handled(self, worker_id: WorkerId) -> Path:
        return self.worker_inbox(worker_id) / "handled"

    def worker_events(self, worker_id: WorkerId) -> Path:
        return self.worker_dir(worker_id) / "events.jsonl"

    def worker_takeover(self, worker_id: WorkerId) -> Path:
        return self.worker_dir(worker_id) / "takeover"

    # approvals
    @property
    def approvals_dir(self) -> Path:
        return self.root / "approvals"

    def approval(self, approval_id: str) -> Path:
        return self.approvals_dir / f"{approval_id}.json"

    def approval_decision(self, approval_id: str) -> Path:
        return self.approvals_dir / f"{approval_id}.decision.json"
