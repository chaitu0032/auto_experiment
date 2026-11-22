"""Fold telemetry events into a CurveSummary (pure functions)."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import replace

from autoexp.core.ids import RunId
from autoexp.core.models import CurveSummary, TelemetryEvent, TelemetryKind

MAX_CURVE_POINTS = 600


def empty_summary(run_id: RunId) -> CurveSummary:
    return CurveSummary(
        run_id=run_id,
        step=0,
        episodes=0,
        rolling_return=None,
        best_rolling_return=None,
        best_at_step=None,
        last_eval_return=None,
        approx_kl=None,
        entropy=None,
        value_loss=None,
        grad_norm=None,
        learning_rate=None,
        sps=None,
        rss_mb=None,
        nan_seen=False,
        kl_over_limit_streak=0,
        malformed_lines=0,
    )


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _non_finite(values: dict[str, object] | object) -> bool:
    if not isinstance(values, dict):
        return False
    return any(isinstance(v, float) and not math.isfinite(v) for v in values.values())


def thin(points: tuple[tuple[int, float], ...], limit: int) -> tuple[tuple[int, float], ...]:
    """Keep at most `limit` points: drop every other point, always keep the last."""
    if len(points) <= limit:
        return points
    kept = points[::2]
    if kept[-1] != points[-1]:
        kept = (*kept, points[-1])
    return kept


def summarize(
    run_id: RunId,
    events: Iterable[TelemetryEvent],
    *,
    window: int,
    kl_max: float,
    previous: CurveSummary | None = None,
    malformed: int = 0,
) -> CurveSummary:
    """Return a new summary extending `previous` with `events`.

    Computes the rolling mean of the last `window` episode returns, the best
    rolling mean and its step, latest update metrics, NaN/inf detection over all
    numeric fields, and the current streak of updates with approx_kl > kl_max.
    """
    s = previous or empty_summary(run_id)
    recent = list(s.recent_returns)
    curve = list(s.curve)
    fields = {}
    for event in events:
        values = dict(event.values)
        step = max(s.step, event.step)
        nan_seen = s.nan_seen or _non_finite(values)
        s = replace(s, step=step, nan_seen=nan_seen)
        if event.kind is TelemetryKind.EPISODE:
            ret = _num(values.get("return"))
            if ret is None or not math.isfinite(ret):
                continue
            recent.append(ret)
            if len(recent) > window:
                del recent[: len(recent) - window]
            rolling = sum(recent) / len(recent)
            best, best_at = s.best_rolling_return, s.best_at_step
            # Only a full window counts toward "best" so early lucky episodes do not.
            if len(recent) >= min(window, 10) and (best is None or rolling > best):
                best, best_at = rolling, event.step
            curve.append((event.step, rolling))
            s = replace(
                s,
                episodes=s.episodes + 1,
                rolling_return=rolling,
                best_rolling_return=best,
                best_at_step=best_at,
            )
        elif event.kind is TelemetryKind.UPDATE:
            kl = _num(values.get("approx_kl"))
            value_loss = _num(values.get("value_loss"))
            streak = s.kl_over_limit_streak
            if kl is not None:
                streak = streak + 1 if (not math.isfinite(kl) or kl > kl_max) else 0
            peak = s.value_loss_peak
            if value_loss is not None and (peak is None or value_loss > peak):
                peak = value_loss
            s = replace(
                s,
                approx_kl=kl,
                entropy=_num(values.get("entropy")),
                value_loss=value_loss,
                grad_norm=_num(values.get("grad_norm")),
                learning_rate=_num(values.get("lr")),
                sps=_num(values.get("sps")),
                kl_over_limit_streak=streak,
                value_loss_peak=peak,
            )
        elif event.kind is TelemetryKind.EVAL:
            s = replace(s, last_eval_return=_num(values.get("mean_return")))
        elif event.kind is TelemetryKind.SYSTEM:
            s = replace(s, rss_mb=_num(values.get("rss_mb")))
    if len(curve) > MAX_CURVE_POINTS:
        fields["curve"] = thin(tuple(curve), MAX_CURVE_POINTS // 2)
    else:
        fields["curve"] = tuple(curve)
    return replace(
        s,
        recent_returns=tuple(recent),
        malformed_lines=s.malformed_lines + malformed,
        **fields,
    )


def best_until(summary: CurveSummary, step: int) -> float | None:
    """Best rolling return reached at or before `step` (None if no point yet)."""
    values = [r for (st, r) in summary.curve if st <= step]
    return max(values) if values else None
