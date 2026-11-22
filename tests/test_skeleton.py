"""Skeleton checks: every module imports, the real pieces behave, fakes satisfy
their protocols. Behavioural tests for each component land with the build."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import autoexp
from autoexp.cli import build_parser, main
from autoexp.core.ids import format_run_id, parse_run_id, validate_device_id, validate_worker_id
from autoexp.core.models import TERMINAL_STATUSES, BudgetLine, RunStatus
from autoexp.herdr.adapter import AgentState, HerdrAdapter
from autoexp.herdr.fake import FakeHerdr
from autoexp.state.layout import StateLayout


def test_every_module_imports() -> None:
    names = [
        m.name
        for m in pkgutil.walk_packages(autoexp.__path__, "autoexp.")
        if m.name != "autoexp.__main__"
    ]
    assert len(names) > 30
    for name in names:
        importlib.import_module(name)


def test_run_id_round_trip() -> None:
    assert format_run_id(3) == "r0003"
    assert parse_run_id("r0003") == 3
    assert parse_run_id(format_run_id(12345)) == 12345


@pytest.mark.parametrize("bad", ["", "r1", "x0001", "r00a1"])
def test_parse_run_id_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_run_id(bad)


def test_format_run_id_rejects_zero() -> None:
    with pytest.raises(ValueError):
        format_run_id(0)


def test_worker_and_device_ids() -> None:
    assert validate_worker_id("w1") == "w1"
    assert validate_device_id("cpu0") == "cpu0"
    with pytest.raises(ValueError):
        validate_worker_id("worker-1")
    with pytest.raises(ValueError):
        validate_device_id("gpu0")


def test_layout_is_keyed_by_run_id(tmp_path: Path) -> None:
    layout = StateLayout(tmp_path)
    run = format_run_id(7)
    assert layout.run_metrics(run) == tmp_path / "runs" / "r0007" / "metrics.jsonl"
    assert layout.run_lock(run) == tmp_path / "locks" / "run-r0007.lock"
    assert StateLayout.lock_info(layout.master_lock) == tmp_path / "locks" / "master.json"


def test_terminal_statuses() -> None:
    assert RunStatus.PRUNED.is_terminal
    assert not RunStatus.RUNNING.is_terminal
    assert RunStatus.ALLOCATED not in TERMINAL_STATUSES


def test_budget_line() -> None:
    line = BudgetLine(name="wall_clock_s", used=900.0, limit=3600.0)
    assert line.fraction == 0.25
    assert not line.exhausted
    assert BudgetLine(name="llm_calls", used=20, limit=20).exhausted


def test_fake_herdr_satisfies_protocol() -> None:
    fake = FakeHerdr()
    assert isinstance(fake, HerdrAdapter)
    pane = fake.create_workspace("autoexp", "/tmp")
    worker = fake.split_pane(pane.pane_id, "right", "/tmp", {})
    fake.send_text(worker.pane_id, "hello")
    fake.send_keys(worker.pane_id, "Enter")
    assert fake.sent(worker.pane_id) == ("text:hello", "keys:Enter")
    assert fake.agent_state(worker.pane_id) is AgentState.IDLE


def test_cli_parses_and_reports_skeleton(capsys: pytest.CaptureFixture[str]) -> None:
    args = build_parser().parse_args(["worker", "handle", "--worker", "w1"])
    assert args.worker_command == "handle"
    assert main(["status"]) == 2
    assert "not implemented yet" in capsys.readouterr().err
