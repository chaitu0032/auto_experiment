"""Telemetry writer, tolerant reader, and summary folding."""

from __future__ import annotations

import math
from pathlib import Path

from autoexp.core.ids import RunId
from autoexp.core.models import TelemetryEvent, TelemetryKind
from autoexp.state.layout import StateLayout
from autoexp.telemetry.reader import TelemetryReader, parse_event
from autoexp.telemetry.summary import best_until, summarize, thin
from autoexp.telemetry.writer import TelemetryWriter

R1 = RunId("r0001")


def _ep(step: int, ret: float) -> TelemetryEvent:
    return TelemetryEvent(R1, 0.0, step, TelemetryKind.EPISODE, {"return": ret, "length": 10})


def _up(step: int, **values: float) -> TelemetryEvent:
    return TelemetryEvent(R1, 0.0, step, TelemetryKind.UPDATE, values)


def test_writer_reader_round_trip_with_nan(layout: StateLayout) -> None:
    path = layout.run_metrics(R1)
    with TelemetryWriter(path, R1) as w:
        w.episode(10, 20.0, 20)
        w.update(
            10,
            policy_loss=0.1,
            value_loss=float("nan"),
            entropy=0.6,
            approx_kl=0.01,
            clipfrac=0.1,
            grad_norm=0.5,
            lr=1e-3,
            explained_variance=0.2,
            sps=100.0,
        )
        w.eval(10, 30.0, 1.0, 5)
        w.system(10, 90.0, 120.0, 2)
    batch = TelemetryReader(layout).read_new(R1, 0)
    assert [e.kind for e in batch.events] == [
        TelemetryKind.EPISODE,
        TelemetryKind.UPDATE,
        TelemetryKind.EVAL,
        TelemetryKind.SYSTEM,
    ]
    assert math.isnan(batch.events[1].values["value_loss"])  # type: ignore[arg-type]
    assert batch.events[2].values["video_path"] is None
    assert TelemetryReader(layout).read_new(R1, batch.next_offset).events == ()


def test_reader_counts_bad_lines_and_waits_for_partial(layout: StateLayout) -> None:
    path = layout.run_metrics(R1)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"t":1,"step":5,"kind":"episode","values":{"return":9}}\n'
        '{"t":1,"step":5,"kind":"bogus","values":{}}\n'
        '{"t":1,"step":-1,"kind":"episode","values":{}}\n'
        '{"t":1,"step":6,"kind":"epis'
    )
    batch = TelemetryReader(layout).read_new(R1, 0)
    assert len(batch.events) == 1 and batch.malformed == 2


def test_parse_event_validation() -> None:
    assert (
        parse_event(R1, {"t": 1, "step": 1, "kind": "episode", "values": {"return": "inf"}})
        is not None
    )
    assert parse_event(R1, {"t": "x", "step": 1, "kind": "episode", "values": {}}) is None
    assert parse_event(R1, {"t": 1, "step": True, "kind": "episode", "values": {}}) is None
    assert parse_event(R1, {"t": 1, "step": 1, "kind": "episode", "values": []}) is None


def test_summary_rolling_best_and_incremental() -> None:
    events = [_ep(i * 10, float(i)) for i in range(1, 21)]
    first = summarize(R1, events[:10], window=5, kl_max=0.5)
    assert first.episodes == 10 and first.rolling_return == sum(range(6, 11)) / 5
    full = summarize(R1, events[10:], window=5, kl_max=0.5, previous=first)
    assert full.rolling_return == sum(range(16, 21)) / 5
    assert full.best_rolling_return == full.rolling_return and full.best_at_step == 200
    assert full.step == 200
    assert best_until(full, 100) == first.best_rolling_return


def test_summary_kl_streak_nan_and_peak() -> None:
    s = summarize(
        R1, [_up(1, approx_kl=0.9), _up(2, approx_kl=0.8, value_loss=5.0)], window=5, kl_max=0.5
    )
    assert s.kl_over_limit_streak == 2 and s.value_loss_peak == 5.0
    s = summarize(R1, [_up(3, approx_kl=0.1)], window=5, kl_max=0.5, previous=s)
    assert s.kl_over_limit_streak == 0 and not s.nan_seen
    s = summarize(R1, [_up(4, value_loss=float("nan"))], window=5, kl_max=0.5, previous=s)
    assert s.nan_seen


def test_thin_keeps_last_point() -> None:
    points = tuple((i, float(i)) for i in range(11))
    thinned = thin(points, 5)
    assert thinned[-1] == (10, 10.0) and len(thinned) <= 7
    assert thin(points, 50) == points


def test_writer_creates_parent(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "metrics.jsonl"
    with TelemetryWriter(path, R1) as w:
        w.episode(1, 1.0, 1, success=True)
    assert path.exists()
