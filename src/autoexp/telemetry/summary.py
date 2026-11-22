"""Fold telemetry events into a CurveSummary (pure functions)."""

from __future__ import annotations

from collections.abc import Iterable

from autoexp.core.ids import RunId
from autoexp.core.models import CurveSummary, TelemetryEvent


def summarize(
    run_id: RunId,
    events: Iterable[TelemetryEvent],
    *,
    window: int,
    kl_max: float,
    previous: CurveSummary | None = None,
) -> CurveSummary:
    """Return a new summary extending `previous` with `events`.

    Computes the rolling mean of the last `window` episode returns, the best
    rolling mean and its step, latest update metrics, NaN/inf detection over all
    numeric fields, and the current streak of updates with approx_kl > kl_max.
    """
    raise NotImplementedError
