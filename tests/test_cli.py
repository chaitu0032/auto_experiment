"""CLI commands over a state directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoexp.cli import CliError, main, resolve_root
from autoexp.core.ids import WorkerId
from autoexp.core.models import ApprovalKind
from autoexp.state.approvals import FileApprovalStore
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout
from autoexp.worker.state import has_takeover, worker_events


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "state" / "exp"
    write_json_atomic(
        StateLayout(root).experiment_file,
        {
            "experiment_id": "exp",
            "started_at": 0.0,
            "trainer": {"name": "fake", "command": ["python"], "total_steps": 10},
            "devices": {"slots": ["cpu0"], "threads_per_slot": 1},
            "goal": {
                "metric": "rolling_return",
                "window": 10,
                "threshold": 475.0,
                "seeds_required": 3,
            },
        },
    )
    return root


def test_resolve_root(root: Path, tmp_path: Path) -> None:
    assert resolve_root(str(root)) == root.resolve()
    assert resolve_root(str(root.parent)) == root.resolve()
    with pytest.raises(CliError, match="no experiment"):
        resolve_root(str(tmp_path / "empty"))
    StateLayout(root.parent / "other").experiment_file.parent.mkdir(parents=True)
    write_json_atomic(StateLayout(root.parent / "other").experiment_file, {})
    with pytest.raises(CliError, match="several"):
        resolve_root(str(root.parent))


def test_stop_status_approve(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--state-dir", str(root), "stop"]) == 0
    assert StateLayout(root).stop_flag.exists()
    store = FileApprovalStore(StateLayout(root))
    request = store.request(ApprovalKind.BUDGET_EXTENSION, "more", {}, "master")
    assert main(["--state-dir", str(root), "status"]) == 0
    assert "PENDING" in capsys.readouterr().out
    assert main(["--state-dir", str(root), "approve", request.approval_id]) == 0
    assert main(["--state-dir", str(root), "reject", request.approval_id]) == 2
    assert "already approved" in capsys.readouterr().err


def test_worker_report_takeover_handle(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "--state-dir",
                str(root),
                "worker",
                "report",
                "--worker",
                "w1",
                "--state",
                "blocked",
                "--run",
                "r0001",
                "--verdict",
                "unfixable",
                "bad config",
            ]
        )
        == 0
    )
    events, _ = worker_events(StateLayout(root), WorkerId("w1"))
    assert events[-1].verdict == "unfixable" and events[-1].note == "bad config"
    assert main(["--state-dir", str(root), "worker", "takeover", "w1", "on"]) == 0
    assert has_takeover(StateLayout(root), WorkerId("w1"))
    assert main(["--state-dir", str(root), "worker", "handle", "--worker", "w1"]) == 0
    assert "inbox empty" in capsys.readouterr().out
    assert main(["--state-dir", str(root), "worker", "handle", "--worker", "bad id"]) == 2


def test_up_rejects_bad_config(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("experiment_id = 1")
    assert main(["--state-dir", str(tmp_path), "up", "--config", str(bad), "--no-herdr"]) == 2
