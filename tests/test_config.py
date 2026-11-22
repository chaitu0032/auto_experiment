"""Config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoexp.config.loader import ConfigError, load_config, parse_config
from autoexp.config.schema import EngineKind, HalvingMode, WorkerRuntimeKind
from tests.conftest import ROOT, config_dict


def test_milestone_config_loads() -> None:
    config = load_config(ROOT / "configs" / "cartpole.toml")
    assert len(config.initial_configs) == 4
    assert config.workers.count == 2 and config.workers.runs_per_worker == 2
    assert config.devices.max_concurrent_runs == 4
    assert config.pruning.halving.mode is HalvingMode.SYNC
    assert (
        config.llm.engine is EngineKind.LLM and config.workers.runtime is WorkerRuntimeKind.CLAUDE
    )
    assert config.goal.seeds_required == 3 and config.goal.threshold == 475.0


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"workers.count": 3}, "exceeds devices.max_concurrent_runs"),
        ({"devices.max_concurrent_runs": 9}, "cannot exceed"),
        ({"dashboard.host": "0.0.0.0"}, "loopback"),
        ({"pruning.halving.rungs": [0.5, 0.25]}, "increasing"),
        ({"pruning.order": ["magic"]}, "unknown pruners"),
        ({"budget.wall_clock_s": 0}, "must be > 0"),
        ({"llm.engine": "gpt"}, "llm.engine"),
        ({"workers.runtime": "tmux"}, "workers.runtime"),
        ({"devices.slots": ["gpu0"]}, "device"),
        ({"search_space.learning_rate": {"kind": "log_float", "low": 0, "high": 1}}, "> 0"),
        ({"trainer.total_steps": "many"}, "must be int"),
    ],
)
def test_invalid_configs(override: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(config_dict(**override))


def test_initial_config_outside_space() -> None:
    data = config_dict()
    data["initial_configs"][0]["learning_rate"] = 1.0
    with pytest.raises(ConfigError, match="outside the search space"):
        parse_config(data)
    data = config_dict()
    data["initial_configs"][0]["mystery"] = 1
    with pytest.raises(ConfigError, match="unknown parameters"):
        parse_config(data)


def test_missing_file_and_bad_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("x = [")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(bad)
