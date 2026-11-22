"""API handlers: read the state directory, return DTOs from schemas.py.

Reads go through `views.py` (pure builders over files). The three mutating
handlers write only: an approval decision file, a worker takeover marker, or
the STOP flag. They never talk to Herdr or to workers; the master picks the
change up on its next tick.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from autoexp.dashboard.schemas import (
    ApprovalAnswerIn,
    ApprovalDTO,
    CurveDTO,
    EventsDTO,
    OverviewDTO,
    PaneDTO,
    RunDetailDTO,
    TakeoverIn,
    WorkerDTO,
)
from autoexp.state.layout import StateLayout


def get_overview(layout: StateLayout) -> OverviewDTO:
    raise NotImplementedError


def get_run(layout: StateLayout, run_id: str) -> RunDetailDTO:
    raise NotImplementedError


def get_curve(layout: StateLayout, run_id: str, query: Mapping[str, str]) -> CurveDTO:
    """`?since_step=N` returns only points after N; `?max_points=M` downsamples."""
    raise NotImplementedError


def get_events(layout: StateLayout, query: Mapping[str, str]) -> EventsDTO:
    """`?after=SEQ` returns events after SEQ; `?kinds=prune,decision` filters."""
    raise NotImplementedError


def get_pane(layout: StateLayout, worker_id: str, query: Mapping[str, str]) -> PaneDTO:
    raise NotImplementedError


def stream(layout: StateLayout) -> Iterator[str]:
    """Server-sent events: yields `event: version\\ndata: {"version": N}\\n\\n`
    whenever the overview version changes, and a `: keepalive` comment every
    15 s. The client refetches what it shows when the version moves."""
    raise NotImplementedError


def answer_approval(layout: StateLayout, approval_id: str, body: ApprovalAnswerIn) -> ApprovalDTO:
    """409 conflict if the approval is no longer pending."""
    raise NotImplementedError


def set_takeover(layout: StateLayout, worker_id: str, body: TakeoverIn) -> WorkerDTO:
    raise NotImplementedError


def request_stop(layout: StateLayout) -> OverviewDTO:
    raise NotImplementedError
