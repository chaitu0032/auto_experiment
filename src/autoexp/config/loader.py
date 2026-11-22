"""Load and validate a TOML experiment config.

Validation fails fast with a message naming the bad key, and checks cross-field
rules: initial configs lie inside the search space, `workers.count *
workers.runs_per_worker <= devices.max_concurrent_runs <= len(devices.slots)`,
halving rungs are increasing fractions in (0, 1), budgets are positive, the
dashboard host is a loopback address.
"""

from __future__ import annotations

from pathlib import Path

from autoexp.config.schema import ExperimentConfig


class ConfigError(ValueError):
    """Raised with a user-facing message when a config is invalid."""


def load_config(path: Path) -> ExperimentConfig:
    """Parse `path` with tomllib and return a validated ExperimentConfig."""
    raise NotImplementedError("config loading is implemented in the build phase")
