"""Trainer contract.

A trainer is any command that accepts:

    --run-dir DIR --run-id ID --config JSON --seed N --total-steps N
    --device DEV --threads N

writes TelemetryEvent lines to DIR/metrics.jsonl (telemetry/writer.py),
checkpoints to DIR/checkpoints/, exits 0 when its budget is done, and exits
promptly with code 0 on SIGTERM after flushing telemetry.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from autoexp.core.models import ParamValue, RunSpec


@dataclass(frozen=True, slots=True)
class TrainerInvocation:
    argv: tuple[str, ...]
    env: Mapping[str, str]


def config_json(config: Mapping[str, ParamValue]) -> str:
    return json.dumps(dict(config), sort_keys=True, separators=(",", ":"))


def device_kind(device_id: str) -> str:
    """Torch device for a slot id: cpu0 -> cpu, mps0 -> mps."""
    return "mps" if device_id.startswith("mps") else "cpu"


def build_invocation(
    command: tuple[str, ...], spec: RunSpec, run_dir: str, device: str, threads: int
) -> TrainerInvocation:
    argv = (
        *command,
        "--run-dir",
        run_dir,
        "--run-id",
        spec.run_id,
        "--config",
        config_json(spec.config),
        "--seed",
        str(spec.seed),
        "--total-steps",
        str(spec.total_steps),
        "--device",
        device_kind(device),
        "--threads",
        str(threads),
    )
    thread_env = {
        "OMP_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads),
        "PYTHONUNBUFFERED": "1",
    }
    return TrainerInvocation(argv=argv, env={**os.environ, **thread_env})
