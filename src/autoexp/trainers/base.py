"""Trainer contract.

A trainer is any command that accepts:

    --run-dir DIR --run-id ID --config JSON --seed N --total-steps N
    --device DEV --threads N

writes TelemetryEvent lines to DIR/metrics.jsonl (telemetry/writer.py),
checkpoints to DIR/checkpoints/, exits 0 when its budget is done, and exits
promptly with code 0 on SIGTERM after flushing telemetry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.core.models import ParamValue, RunSpec


@dataclass(frozen=True, slots=True)
class TrainerInvocation:
    argv: tuple[str, ...]
    env: Mapping[str, str]


def build_invocation(
    command: tuple[str, ...], spec: RunSpec, run_dir: str, device: str, threads: int
) -> TrainerInvocation:
    raise NotImplementedError


def config_json(config: Mapping[str, ParamValue]) -> str:
    raise NotImplementedError
