"""Trainer-side telemetry writer (part of the trainer contract).

Any trainer that writes these lines is a valid trainer. Kinds and fields are
listed in docs/architecture.md 3.4; `eval.video_path` is the rollout-video seam
and stays None in v1. Non-finite floats are written as the strings "nan",
"inf" or "-inf" so the master can see divergence (JSON has no NaN).
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from types import TracebackType
from typing import IO

from autoexp.core.ids import RunId


def _clean(value: float | int | str | bool | None) -> float | int | str | bool | None:
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    return value


class TelemetryWriter:
    def __init__(self, metrics_path: Path, run_id: RunId) -> None:
        self._path = metrics_path
        self._run_id = run_id
        self._handle: IO[str] | None = None

    def _write(self, kind: str, step: int, values: dict[str, float | int | str | None]) -> None:
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a", encoding="utf-8", buffering=1)
        record = {
            "run_id": self._run_id,
            "t": time.time(),
            "step": int(step),
            "kind": kind,
            "values": {k: _clean(v) for k, v in values.items()},
        }
        self._handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def episode(
        self, step: int, episode_return: float, length: int, success: bool | None = None
    ) -> None:
        values: dict[str, float | int | str | None] = {"return": episode_return, "length": length}
        if success is not None:
            values["success"] = int(success)
        self._write("episode", step, values)

    def update(
        self,
        step: int,
        *,
        policy_loss: float,
        value_loss: float,
        entropy: float,
        approx_kl: float,
        clipfrac: float,
        grad_norm: float,
        lr: float,
        explained_variance: float,
        sps: float,
    ) -> None:
        self._write(
            "update",
            step,
            {
                "policy_loss": policy_loss,
                "value_loss": value_loss,
                "entropy": entropy,
                "approx_kl": approx_kl,
                "clipfrac": clipfrac,
                "grad_norm": grad_norm,
                "lr": lr,
                "explained_variance": explained_variance,
                "sps": sps,
            },
        )

    def eval(
        self,
        step: int,
        mean_return: float,
        std_return: float,
        episodes: int,
        video_path: str | None = None,
    ) -> None:
        self._write(
            "eval",
            step,
            {
                "mean_return": mean_return,
                "std_return": std_return,
                "episodes": episodes,
                "video_path": video_path,
            },
        )

    def system(
        self,
        step: int,
        cpu_percent: float,
        rss_mb: float,
        threads: int,
        device_util: float | None = None,
    ) -> None:
        self._write(
            "system",
            step,
            {
                "cpu_percent": cpu_percent,
                "rss_mb": rss_mb,
                "threads": threads,
                "device_util": device_util,
            },
        )

    def close(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
            self._handle = None

    def __enter__(self) -> TelemetryWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
