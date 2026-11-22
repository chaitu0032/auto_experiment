"""The real PPO trainer (short), trainer invocation, worker runtimes, shell serve loop."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import InboxKind, RunPurpose, RunSpec
from autoexp.herdr.cli_adapter import HerdrError
from autoexp.herdr.null import NullHerdr
from autoexp.state.inbox import FileInbox
from autoexp.state.layout import StateLayout
from autoexp.trainers.base import build_invocation, config_json, device_kind
from autoexp.worker.brief import render_brief
from autoexp.worker.runtime import ClaudeCodeRuntime, ShellRuntime
from autoexp.worker.serve import serve
from autoexp.worker.state import worker_events

torch = pytest.importorskip("torch")
pytest.importorskip("gymnasium")


def test_ppo_trainer_writes_contract_telemetry(tmp_path: Path) -> None:
    from autoexp.trainers.ppo_cartpole import main, parse_hyper

    config = {
        "learning_rate": 1e-3,
        "num_envs": 2,
        "num_steps": 64,
        "eval_every_updates": 4,
        "eval_episodes": 1,
    }
    code = main(
        [
            "--run-dir",
            str(tmp_path),
            "--run-id",
            "r0001",
            "--config",
            json.dumps(config),
            "--seed",
            "1",
            "--total-steps",
            "1280",
            "--threads",
            "1",
        ]
    )
    assert code == 0
    lines = [json.loads(line) for line in (tmp_path / "metrics.jsonl").read_text().splitlines()]
    kinds = {line["kind"] for line in lines}
    assert {"episode", "update", "eval", "system"} <= kinds
    update = next(line for line in lines if line["kind"] == "update")["values"]
    assert {"policy_loss", "value_loss", "entropy", "approx_kl", "grad_norm", "lr", "sps"} <= set(
        update
    )
    assert (tmp_path / "checkpoints" / "latest.pt").exists()
    with pytest.raises(ValueError, match="unknown hyperparameters"):
        parse_hyper({"warp_drive": 1})


def test_build_invocation() -> None:
    spec = RunSpec(RunId("r0002"), "e", 1, "ppo", {"b": 2, "a": 1}, 3, 100, RunPurpose.EXPLORE)
    inv = build_invocation(("python", "-m", "t"), spec, "/runs/r0002", "cpu1", 2)
    assert inv.argv[:3] == ("python", "-m", "t") and "--threads" in inv.argv
    assert (
        inv.argv[inv.argv.index("--config") + 1] == config_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'
    )
    assert inv.env["OMP_NUM_THREADS"] == "2" and device_kind("mps0") == "mps"


def test_runtimes_and_brief() -> None:
    claude = ClaudeCodeRuntime(("claude",), "/py -m autoexp --state-dir /s")
    argv = shlex.split(claude.start_command(WorkerId("w1"), "/s", "/s/workers/w1/BRIEF.md"))
    assert argv[0] == "claude" and "--allowedTools" in argv and argv[-2] == "--"
    assert "Bash(/py -m autoexp --state-dir /s worker:*)" in argv
    assert "/s/workers/w1/BRIEF.md" in argv[-1]
    shell = ShellRuntime("/py -m autoexp --state-dir /s")
    assert (
        shell.start_command(WorkerId("w2"), "/s", "")
        == "/py -m autoexp --state-dir /s worker serve --worker w2"
    )
    brief = render_brief(WorkerId("w1"), "/s", "/py -m autoexp")
    assert "worker handle --worker w1" in brief and "--verdict retry" in brief


def test_serve_loop_handles_inbox_and_declines_debug(tmp_path: Path) -> None:
    layout = StateLayout(tmp_path)
    inbox = FileInbox(layout)
    inbox.post(
        WorkerId("w1"), "r0001-debug", InboxKind.DEBUG, {"run_id": "r0001", "reason": "boom"}
    )
    inbox.post(WorkerId("w1"), "n1", InboxKind.NOTE, {"text": "hello"})
    ticks = iter([True, True, False])
    serve(layout, WorkerId("w1"), poll_s=0.01, should_continue=lambda: next(ticks))
    events, _ = worker_events(layout, WorkerId("w1"))
    assert events[-1].verdict == "unfixable" and events[-1].run_id == "r0001"
    assert inbox.pending(WorkerId("w1")) == ()


def test_null_herdr() -> None:
    null = NullHerdr()
    assert null.read("p", 5) == "" and not null.pane_exists("p")
    with pytest.raises(HerdrError):
        null.send_text("p", "x")
