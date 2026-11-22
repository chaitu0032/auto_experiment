"""Open and re-open worker panes in Herdr and start each worker's runtime."""

from __future__ import annotations

import logging

from autoexp.config.schema import WorkersConfig
from autoexp.core.ids import WorkerId
from autoexp.core.models import WorkerRecord, WorkerState
from autoexp.herdr.adapter import HerdrAdapter
from autoexp.herdr.cli_adapter import HerdrError
from autoexp.state.layout import StateLayout
from autoexp.worker.brief import render_brief
from autoexp.worker.runtime import WorkerRuntime
from autoexp.worker.state import list_workers, read_worker, write_worker

log = logging.getLogger(__name__)


class WorkerPanes:
    def __init__(
        self,
        layout: StateLayout,
        herdr: HerdrAdapter | None,
        workers: WorkersConfig,
        runtime: WorkerRuntime,
        autoexp_command: str,
        cwd: str,
        workspace_label: str,
    ) -> None:
        self._layout = layout
        self._herdr = herdr
        self._workers = workers
        self._runtime = runtime
        self._cmd = autoexp_command
        self._cwd = cwd
        self._label = workspace_label

    def worker_ids(self) -> tuple[WorkerId, ...]:
        return tuple(WorkerId(f"w{i}") for i in range(1, self._workers.count + 1))

    def _alive(self, record: WorkerRecord | None) -> bool:
        if record is None or record.pane_id is None or self._herdr is None:
            return False
        try:
            return self._herdr.pane_exists(record.pane_id)
        except HerdrError:
            return False

    def ensure(self) -> tuple[WorkerRecord, ...]:
        """Every configured worker has a live pane running its runtime.

        Returns the worker records; `opened` panes were (re)started this call.
        Without Herdr, records are written with no pane and workers are expected
        to be started by hand (`autoexp worker serve`).
        """
        anchor: str | None = None
        for worker_id in self.worker_ids():
            record = read_worker(self._layout, worker_id)
            if self._alive(record):
                anchor = anchor or (record.pane_id if record else None)
                continue
            brief_path = self._layout.worker_brief(worker_id)
            brief_path.parent.mkdir(parents=True, exist_ok=True)
            brief_path.write_text(
                render_brief(worker_id, str(self._layout.root), self._cmd), encoding="utf-8"
            )
            pane_id = None
            if self._herdr is not None:
                pane_id = self._open(worker_id, anchor)
                anchor = anchor or pane_id
            write_worker(
                self._layout,
                WorkerRecord(
                    worker_id=worker_id,
                    pane_id=pane_id,
                    runtime=self._runtime.name,
                    state=WorkerState.IDLE,
                    takeover=False,
                    run_ids=(),
                ),
            )
        return list_workers(self._layout)

    def _open(self, worker_id: WorkerId, anchor: str | None) -> str:
        assert self._herdr is not None
        if anchor is None:
            pane = self._herdr.create_workspace(self._label, self._cwd)
        else:
            pane = self._herdr.split_pane(anchor, "right", self._cwd, {"AUTOEXP_WORKER": worker_id})
        try:
            self._herdr.rename(pane.pane_id, f"autoexp-{worker_id}")
        except HerdrError as err:
            log.warning("could not label pane %s: %s", pane.pane_id, err)
        command = self._runtime.start_command(
            worker_id, str(self._layout.root), str(self._layout.worker_brief(worker_id))
        )
        self._herdr.run(pane.pane_id, command)
        return pane.pane_id
