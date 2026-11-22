"""Trainer-side telemetry writer (part of the trainer contract).

Any trainer that writes these lines is a valid trainer. Kinds and fields are
listed in docs/architecture.md 3.4; `eval.video_path` is the rollout-video seam
and stays None in v1.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

from autoexp.core.ids import RunId


class TelemetryWriter:
    def __init__(self, metrics_path: Path, run_id: RunId) -> None:
        self._path = metrics_path
        self._run_id = run_id

    def episode(
        self, step: int, episode_return: float, length: int, success: bool | None = None
    ) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    def eval(
        self,
        step: int,
        mean_return: float,
        std_return: float,
        episodes: int,
        video_path: str | None = None,
    ) -> None:
        raise NotImplementedError

    def system(
        self,
        step: int,
        cpu_percent: float,
        rss_mb: float,
        threads: int,
        device_util: float | None = None,
    ) -> None:
        raise NotImplementedError

    def __enter__(self) -> TelemetryWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None
