"""Master loop end to end with real run supervisors and the fake trainer.

No Herdr (FakeHerdr records pane calls) and no LLM (deterministic engine);
the shell workers are simulated by threads that run the real inbox handler.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from autoexp.config.loader import parse_config
from autoexp.config.schema import ExperimentConfig
from autoexp.core.ids import WorkerId
from autoexp.core.models import MasterEventKind, RunPurpose, RunStatus
from autoexp.herdr.fake import FakeHerdr
from autoexp.master.app import BuildOptions, build_master, write_experiment
from autoexp.master.events import MasterEventLog
from autoexp.master.loop import MasterAlreadyFinished
from autoexp.state.inbox import FileInbox
from autoexp.state.jsonl import read_json
from autoexp.state.layout import StateLayout
from autoexp.state.locks import FlockLockManager, LockHeld
from autoexp.state.registry import load_registry
from autoexp.worker.handler import handle_inbox
from tests.conftest import config_dict, fake_trainer_command

CAPS = (100.0, 200.0, 300.0, 490.0)


def loop_config(**overrides: Any) -> ExperimentConfig:
    data = config_dict(
        **{
            "trainer.command": fake_trainer_command(),
            "trainer.total_steps": 10000,
            "goal.window": 10,
            "llm.engine": "deterministic",
            "workers.runtime": "shell",
            "master.tick_s": 0.2,
            "pruning.plateau.min_steps": 10**6,
            "budget.wall_clock_s": 300,
            **overrides,
        }
    )
    data["search_space"]["scale"] = {"kind": "float", "low": 100.0, "high": 5000.0}
    for cfg, cap in zip(data["initial_configs"], CAPS, strict=True):
        cfg.update({"cap": cap, "scale": 500.0, "delay": 0.15})
    return parse_config(data)


class Workers:
    """Threads standing in for shell-worker panes (they run the real handler)."""

    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._serve, args=(WorkerId(w),), daemon=True)
            for w in ("w1", "w2")
        ]

    def _serve(self, worker_id: WorkerId) -> None:
        inbox = FileInbox(self._layout)
        while not self._stop.is_set():
            handle_inbox(self._layout, inbox, worker_id)
            time.sleep(0.1)

    def __enter__(self) -> Workers:
        for t in self._threads:
            t.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)


def _master(config: ExperimentConfig, root: Path):  # type: ignore[no-untyped-def]
    layout = StateLayout(root)
    write_experiment(layout, config, "# test")
    return build_master(
        config, root, BuildOptions(herdr=FakeHerdr(), cwd=str(root), quiet=True)
    ), layout


def _kinds(layout: StateLayout) -> list[MasterEventKind]:
    return [e.kind for e in MasterEventLog(layout, None).all()]


@pytest.fixture(scope="module")
def finished(tmp_path_factory: pytest.TempPathFactory) -> StateLayout:
    root = tmp_path_factory.mktemp("loop") / "exp"
    master, layout = _master(loop_config(), root)
    with Workers(layout):
        verdict = master.run()
    assert verdict.rule == "goal_reached", verdict
    return layout


def test_milestone_shape_rounds_prunes_verification_and_finding(finished: StateLayout) -> None:
    runs = load_registry(finished).all()
    assert all(r.status.is_terminal for r in runs)
    first = [r for r in runs if r.spec.round_index == 1]
    assert len(first) == 4 and {r.spec.purpose for r in first} == {RunPurpose.EXPLORE}
    assert max(r.spec.round_index for r in runs) >= 2, "at least one follow-up round"
    checks = [r for r in runs if r.spec.purpose is RunPurpose.SEED_CHECK]
    assert sorted(r.spec.seed for r in checks) == [2, 3]
    assert any(r.status is RunStatus.PRUNED for r in first), "weak first-round configs are pruned"
    assert any(r.status is RunStatus.SOLVED for r in runs)
    kinds = _kinds(finished)
    assert MasterEventKind.PRUNE in kinds and MasterEventKind.DECISION in kinds
    assert kinds[-1] is MasterEventKind.STOPPED
    finding = read_json(finished.finding_json)
    assert finding["stop_rule"] == "goal_reached"
    assert finding["winner_config"]["cap"] == 490.0
    assert "Winner" in finished.finding_file.read_text()
    devices = {r.device_id for r in runs}
    assert devices <= {"cpu0", "cpu1", "cpu2", "cpu3"}
    assert not FlockLockManager().probe(finished.master_lock)


def test_runs_never_shared_a_device_at_the_same_time(finished: StateLayout) -> None:
    runs = [r for r in load_registry(finished).all() if r.started_at is not None]
    for a in runs:
        for b in runs:
            if a is b or a.device_id != b.device_id:
                continue
            overlap = a.started_at < (b.ended_at or 0) and b.started_at < (a.ended_at or 0)  # type: ignore[operator]
            assert not overlap, (a.spec.run_id, b.spec.run_id)


def test_budget_snapshot_written(finished: StateLayout) -> None:
    budget = read_json(finished.master_budget)
    names = {line["name"] for line in budget["lines"]}
    assert names == {"wall_clock_s", "compute_s", "env_steps", "llm_calls", "llm_cost_usd"}


def test_finished_experiment_does_not_restart(finished: StateLayout) -> None:
    master, _ = _master(loop_config(), finished.root)
    with pytest.raises(MasterAlreadyFinished):
        master.run()


def test_only_one_master(tmp_path: Path) -> None:
    master, layout = _master(loop_config(), tmp_path / "exp")
    held = FlockLockManager().acquire(
        layout.master_lock,
        __import__("autoexp.core.models", fromlist=["LockKind"]).LockKind.MASTER,
        "other",
    )
    try:
        with pytest.raises(LockHeld):
            master.run(max_ticks=1, sleep=False)
    finally:
        FlockLockManager().release(held)


def test_master_crash_and_restart_recovers_every_run(tmp_path: Path) -> None:
    config = loop_config(**{"trainer.total_steps": 20000})
    root = tmp_path / "exp"
    master, layout = _master(config, root)
    with Workers(layout):
        deadline = time.time() + 30
        while time.time() < deadline:
            master.run(max_ticks=3, sleep=True)  # returns as if the master died
            running = [r for r in load_registry(layout).all() if r.status is RunStatus.RUNNING]
            if len(running) >= 2:
                break
        assert len(running) >= 2
        # While the master is down one supervisor dies too.
        victim = running[0]
        holder = FlockLockManager().holder(layout.run_lock(victim.spec.run_id))
        assert holder is not None
        os.kill(holder.holder_pid, signal.SIGKILL)
        time.sleep(0.5)
        restarted, _ = _master(config, root)
        verdict = restarted.run()
    assert verdict.should_stop
    events = MasterEventLog(layout, None).all()
    recovered = [e for e in events if e.kind is MasterEventKind.RECOVERED]
    assert recovered and victim.spec.run_id in recovered[0].data["lost"]
    adopted = recovered[0].data["adopted"]
    assert set(adopted) == {r.spec.run_id for r in running[1:]}
    runs = load_registry(layout).all()
    assert load_registry(layout).get(victim.spec.run_id).status is RunStatus.LOST  # type: ignore[union-attr]
    retries = [r for r in runs if r.spec.purpose is RunPurpose.RETRY]
    assert any(r.spec.parent_run_id == victim.spec.run_id for r in retries)
    assert all(r.status.is_terminal for r in runs)


def test_user_stop_flag_stops_the_loop(tmp_path: Path) -> None:
    master, layout = _master(loop_config(**{"trainer.total_steps": 200000}), tmp_path / "exp")
    with Workers(layout):
        threading.Timer(2.0, lambda: layout.stop_flag.write_text("stop")).start()
        verdict = master.run()
    assert verdict.rule == "user_stop"
    runs = load_registry(layout).all()
    assert all(r.status.is_terminal for r in runs)
    assert {r.status for r in runs} <= {
        RunStatus.STOPPED,
        RunStatus.PRUNED,
        RunStatus.SOLVED,
        RunStatus.LOST,
    }


def test_answered_code_change_is_delivered_to_a_worker(finished: StateLayout) -> None:
    from autoexp.core.models import ApprovalKind, ApprovalStatus, InboxKind
    from autoexp.master import phases
    from autoexp.state.approvals import FileApprovalStore
    from autoexp.worker.state import list_workers

    store = FileApprovalStore(finished)
    request = store.request(
        ApprovalKind.CODE_CHANGE,
        "clip obs",
        {"diff": "--- a"},
        "decision:llm",
        approval_id="code-r1-1",
    )
    master, _ = _master(loop_config(), finished.root)
    deps = master._d  # noqa: SLF001 - exercising one phase directly
    phases.deliver_approval_results(deps, list_workers(finished))
    assert not deps.inbox.exists(WorkerId("w1"), "code-r1-1-result"), "pending is not delivered"
    store.answer(request.approval_id, ApprovalStatus.APPROVED)
    phases.deliver_approval_results(deps, list_workers(finished))
    (message,) = [m for m in deps.inbox.pending(WorkerId("w1")) if m.msg_id == "code-r1-1-result"]
    assert message.kind is InboxKind.APPROVAL_RESULT and message.body["diff"] == "--- a"


def test_launch_timeout_counts_from_assignment_not_allocation(tmp_path: Path) -> None:
    from autoexp.core.ids import DeviceId
    from autoexp.core.models import EventSource, InboxKind, ProposedRun, RunEvent, RunEventKind
    from autoexp.master import phases
    from autoexp.state.recovery import assign_msg_id

    master, _ = _master(loop_config(), tmp_path / "exp")
    deps = master._d  # noqa: SLF001 - exercising one phase directly
    spec = deps.registry.allocate(ProposedRun(config={}, seed=1, purpose=RunPurpose.EXPLORE), 1)
    allocated_at = deps.registry.get(spec.run_id).allocated_at  # type: ignore[union-attr]
    assigned_at = allocated_at + 10 * phases.LAUNCH_TIMEOUT_S
    deps.registry.append(
        RunEvent(
            run_id=spec.run_id,
            at=assigned_at,
            kind=RunEventKind.ASSIGNED,
            source=EventSource.MASTER,
            data={"worker_id": "w1", "device_id": "cpu0"},
        )
    )
    worker = WorkerId("w1")
    message = deps.inbox.post(
        worker,
        assign_msg_id(spec.run_id),
        InboxKind.ASSIGN,
        {"run_id": spec.run_id, "device_id": DeviceId("cpu0")},
    )
    assert message is not None
    deps.inbox.ack(message)

    phases.reconcile(deps, deps.registry.all(), assigned_at + 5.0)
    assert deps.registry.get(spec.run_id).status is RunStatus.ASSIGNED  # type: ignore[union-attr]
    phases.reconcile(deps, deps.registry.all(), assigned_at + phases.LAUNCH_TIMEOUT_S + 1.0)
    assert deps.registry.get(spec.run_id).status is RunStatus.LOST  # type: ignore[union-attr]


def test_runs_lost_to_a_stop_are_not_retried(tmp_path: Path) -> None:
    config = loop_config(**{"devices.max_concurrent_runs": 2, "workers.runs_per_worker": 1})
    master, layout = _master(config, tmp_path / "exp")
    master.run(max_ticks=1, sleep=False)
    queued = [r for r in load_registry(layout).all() if r.status is RunStatus.ALLOCATED]
    assert queued, "runs beyond capacity wait in ALLOCATED"
    layout.stop_flag.write_text("stop")
    master.run(max_ticks=4, sleep=False)
    runs = load_registry(layout).all()
    assert any(r.status is RunStatus.LOST for r in runs)
    assert not [r for r in runs if r.spec.purpose is RunPurpose.RETRY]
