"""Shared test helpers: configs, layouts, synthetic summaries and records."""

from __future__ import annotations

import sys
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest

from autoexp.config.loader import parse_config
from autoexp.config.schema import ExperimentConfig
from autoexp.core.ids import DeviceId, RunId, WorkerId
from autoexp.core.models import (
    CurveSummary,
    Observation,
    RunPurpose,
    RunRecord,
    RunSpec,
    RunStatus,
)
from autoexp.state.layout import StateLayout
from autoexp.telemetry.summary import empty_summary

ROOT = Path(__file__).resolve().parents[1]
FAKE_TRAINER = Path(__file__).parent / "fixtures" / "fake_trainer.py"


def config_dict(**overrides: Any) -> dict[str, Any]:
    data = tomllib.loads((ROOT / "configs" / "cartpole.toml").read_text())
    data["search_space"]["cap"] = {"kind": "float", "low": 0.0, "high": 600.0}
    data["search_space"]["delay"] = {"kind": "float", "low": 0.0, "high": 1.0}
    for cfg in data["initial_configs"]:
        cfg.setdefault("cap", 200.0)
        cfg.setdefault("delay", 0.0)
    for dotted, value in overrides.items():
        node = data
        *path, last = dotted.split(".")
        for key in path:
            node = node[key]
        node[last] = value
    return data


def make_config(**overrides: Any) -> ExperimentConfig:
    return parse_config(config_dict(**overrides))


def fake_trainer_command() -> list[str]:
    return [sys.executable, str(FAKE_TRAINER)]


@pytest.fixture
def layout(tmp_path: Path) -> StateLayout:
    return StateLayout(tmp_path / "state")


def spec(
    n: int,
    *,
    round_index: int = 1,
    config: dict[str, Any] | None = None,
    seed: int = 1,
    purpose: RunPurpose = RunPurpose.EXPLORE,
    total_steps: int = 100_000,
) -> RunSpec:
    return RunSpec(
        run_id=RunId(f"r{n:04d}"),
        experiment_id="test",
        round_index=round_index,
        trainer="fake",
        config=config if config is not None else {"learning_rate": 0.001 * n},
        seed=seed,
        total_steps=total_steps,
        purpose=purpose,
    )


def record(
    n: int,
    status: RunStatus = RunStatus.RUNNING,
    *,
    worker: str | None = "w1",
    device: str | None = "cpu0",
    **spec_kwargs: Any,
) -> RunRecord:
    return RunRecord(
        spec=spec(n, **spec_kwargs),
        status=status,
        worker_id=WorkerId(worker) if worker else None,
        device_id=DeviceId(device) if device else None,
        allocated_at=time.time(),
    )


def summary(
    run_id: str,
    curve: list[tuple[int, float]],
    **fields: Any,
) -> CurveSummary:
    base = empty_summary(RunId(run_id))
    best = max((r for _, r in curve), default=None)
    best_at = next((s for s, r in curve if r == best), None) if best is not None else None
    values = {
        "step": curve[-1][0] if curve else 0,
        "episodes": len(curve),
        "rolling_return": curve[-1][1] if curve else None,
        "best_rolling_return": best,
        "best_at_step": best_at,
        "curve": tuple(curve),
    }
    values.update(fields)
    from dataclasses import replace

    return replace(base, **values)


def observation(rec: RunRecord, summ: CurveSummary | None) -> Observation:
    return Observation(record=rec, summary=summ, traceback=None, pane_excerpt=None)
