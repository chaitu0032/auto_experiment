"""The observability agent (deterministic).

Per tick it builds one `Observation` per live run: the folded RunRecord, an
updated CurveSummary, a traceback found in stdout.log (if any), and an excerpt
of the owning worker's pane. It reads; it never acts.
"""

from __future__ import annotations

from collections.abc import Mapping

from autoexp.core.ids import RunId
from autoexp.core.models import CurveSummary, Observation, RunRecord
from autoexp.herdr.adapter import HerdrAdapter
from autoexp.state.layout import StateLayout
from autoexp.telemetry.reader import TelemetryReader


class Observer:
    def __init__(
        self, layout: StateLayout, telemetry: TelemetryReader, herdr: HerdrAdapter
    ) -> None:
        self._layout = layout
        self._telemetry = telemetry
        self._herdr = herdr

    def observe(
        self, records: tuple[RunRecord, ...], previous: Mapping[RunId, CurveSummary]
    ) -> tuple[Observation, ...]:
        raise NotImplementedError
