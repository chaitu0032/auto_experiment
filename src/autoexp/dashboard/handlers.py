"""API handlers: read the state directory, return DTOs from schemas.py.

Reads go through `views.py` (pure builders over files). The three mutating
handlers write only: an approval decision file, a worker takeover marker, or
the STOP flag. They never talk to Herdr or to workers; the master picks the
change up on its next tick.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from typing import Any

from autoexp.core.ids import RunId, WorkerId
from autoexp.core.models import ApprovalRequest, ApprovalStatus, MasterEventKind
from autoexp.dashboard import views
from autoexp.dashboard.schemas import (
    ApprovalAnswerIn,
    ApprovalDTO,
    BudgetDTO,
    CurveDTO,
    EventsDTO,
    FindingDTO,
    GoalDTO,
    OverviewDTO,
    PaneDTO,
    RunDetailDTO,
    TakeoverIn,
    WorkerDTO,
)
from autoexp.master.events import MasterEventLog
from autoexp.observe.logs import read_tail, tail
from autoexp.state.approvals import ApprovalError, FileApprovalStore
from autoexp.state.codec import encode
from autoexp.state.jsonl import read_json, read_lines
from autoexp.state.layout import StateLayout
from autoexp.state.registry import load_registry
from autoexp.worker.state import list_workers, read_worker
from autoexp.worker.state import set_takeover as mark_takeover

MAX_CURVE_POINTS = 2000
MAX_PANE_LINES = 400
STREAM_POLL_S = 1.0
KEEPALIVE_S = 15.0


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _int_param(query: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, "bad_request", f"{name} must be an integer") from None
    if not low <= value <= high:
        raise ApiError(400, "bad_request", f"{name} must be in {low}..{high}")
    return value


def _approval(a: ApprovalRequest) -> ApprovalDTO:
    return ApprovalDTO(**encode(a))  # type: ignore[typeddict-item]


def _goal(layout: StateLayout) -> GoalDTO:
    experiment = read_json(layout.experiment_file) or {}
    goal = experiment.get("goal", {}) if isinstance(experiment, dict) else {}
    return GoalDTO(
        metric=str(goal.get("metric", "rolling_return")),
        window=int(goal.get("window", 100)),
        threshold=float(goal.get("threshold", 0.0)),
        seeds_required=int(goal.get("seeds_required", 1)),
    )


def get_overview(layout: StateLayout) -> OverviewDTO:
    records = load_registry(layout).all()
    sums = views.summaries(layout)
    experiment = read_json(layout.experiment_file) or {}
    budget_raw = read_json(layout.master_budget)
    budget = None
    if isinstance(budget_raw, dict):
        budget = BudgetDTO(
            at=float(budget_raw.get("at", 0)),
            lines=[
                {
                    "name": line["name"],
                    "used": line["used"],
                    "limit": line["limit"],
                    "fraction": (line["used"] / line["limit"]) if line["limit"] else 0.0,
                }
                for line in budget_raw.get("lines", [])
            ],
        )
    workers = []
    for w in list_workers(layout):
        runs = [
            r.spec.run_id
            for r in records
            if r.worker_id == w.worker_id and not r.status.is_terminal
        ]
        row = views.worker_row(w, runs)
        if row["state"] == "working" and not runs:
            row = WorkerDTO(**{**row, "state": "idle"})  # its runs have all ended
        workers.append(row)
    finding_raw = read_json(layout.finding_json)
    return OverviewDTO(
        experiment_id=str(experiment.get("experiment_id", ""))
        if isinstance(experiment, dict)
        else "",
        goal=_goal(layout),
        version=views.overview_version(layout),
        round_index=max((r.spec.round_index for r in records), default=0),
        master=views.master_status(layout),  # type: ignore[typeddict-item]
        stop_requested=layout.stop_flag.exists(),
        runs=[views.run_row(r, sums.get(r.spec.run_id)) for r in records],
        workers=workers,
        budget=budget,
        approvals=[_approval(a) for a in FileApprovalStore(layout).all()],
        finding=FindingDTO(**finding_raw) if isinstance(finding_raw, dict) else None,  # type: ignore[typeddict-item]
    )


def get_run(layout: StateLayout, run_id: str) -> RunDetailDTO:
    registry = load_registry(layout)
    record = registry.get(RunId(run_id))
    if record is None:
        raise ApiError(404, "not_found", f"no run {run_id}")
    return RunDetailDTO(
        run=views.run_row(record, views.summaries(layout).get(run_id)),
        events=[
            {"at": e.at, "kind": e.kind.value, "source": e.source.value, "data": dict(e.data)}
            for e in registry.events(RunId(run_id))
        ],
        stdout_tail=tail(read_tail(layout.run_stdout(RunId(run_id))), 60),
    )


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def get_curve(layout: StateLayout, run_id: str, query: Mapping[str, str]) -> CurveDTO:
    """`?since_step=N` returns only points after N; `?max_points=M` downsamples."""
    if load_registry(layout).spec(RunId(run_id)) is None:
        raise ApiError(404, "not_found", f"no run {run_id}")
    since = _int_param(query, "since_step", -1, -1, 10**12)
    max_points = _int_param(query, "max_points", 500, 1, MAX_CURVE_POINTS)
    window = _goal(layout)["window"]
    recent: list[float] = []
    returns, losses, last_step = [], [], since
    for rec in read_lines(layout.run_metrics(RunId(run_id))).records:
        step, kind, values = rec.get("step"), rec.get("kind"), rec.get("values") or {}
        if not isinstance(step, int) or not isinstance(values, dict):
            continue
        if kind == "episode":
            ret = _finite(values.get("return"))
            if ret is None:
                continue
            recent = [*recent[-(window - 1) :], ret] if window > 1 else [ret]
            if step > since:
                returns.append(
                    {
                        "step": step,
                        "rolling_return": sum(recent) / len(recent),
                        "episode_return": ret,
                    }
                )
        elif kind == "update" and step > since:
            losses.append(
                {
                    "step": step,
                    "policy_loss": _finite(values.get("policy_loss")),
                    "value_loss": _finite(values.get("value_loss")),
                    "entropy": _finite(values.get("entropy")),
                    "approx_kl": _finite(values.get("approx_kl")),
                }
            )
        last_step = max(last_step, step)
    return CurveDTO(
        run_id=run_id,
        returns=_downsample(returns, max_points),
        losses=_downsample(losses, max_points),
        next_step=last_step,
    )


def _downsample(points: list[Any], limit: int) -> list[Any]:
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    picked = [points[int(i * stride)] for i in range(limit - 1)]
    return [*picked, points[-1]]


def get_events(layout: StateLayout, query: Mapping[str, str]) -> EventsDTO:
    """`?after=SEQ` returns events after SEQ; `?kinds=prune,decision` filters."""
    after = _int_param(query, "after", 0, 0, 10**9)
    kinds_raw = query.get("kinds", "")
    known = {k.value for k in MasterEventKind}
    kinds = {k for k in kinds_raw.split(",") if k} if kinds_raw else None
    if kinds and not kinds <= known:
        raise ApiError(400, "bad_request", f"unknown kinds {sorted(kinds - known)}")
    numbered = MasterEventLog(layout, None).numbered(after)
    events = [
        {"seq": seq, "at": e.at, "kind": e.kind.value, "summary": e.summary, "data": dict(e.data)}
        for seq, e in numbered
        if kinds is None or e.kind.value in kinds
    ]
    next_seq = numbered[-1][0] if numbered else after
    return EventsDTO(events=events, next_seq=next_seq)  # type: ignore[typeddict-item]


def get_pane(layout: StateLayout, worker_id: str, query: Mapping[str, str]) -> PaneDTO:
    lines = _int_param(query, "lines", 60, 1, MAX_PANE_LINES)
    record = read_worker(layout, WorkerId(worker_id))
    if record is None:
        raise ApiError(404, "not_found", f"no worker {worker_id}")
    cached = read_json(layout.worker_pane_tail(WorkerId(worker_id)))
    text = str(cached.get("text", "")) if isinstance(cached, dict) else ""
    read_at = cached.get("read_at") if isinstance(cached, dict) else None
    return PaneDTO(
        worker_id=worker_id,
        pane_id=record.pane_id,
        text=tail(text, lines),
        read_at=float(read_at) if isinstance(read_at, int | float) else None,
    )


def stream(layout: StateLayout, should_continue: Any = lambda: True) -> Iterator[str]:
    """Server-sent events: yields `event: version\\ndata: {"version": N}\\n\\n`
    whenever the overview version changes, and a `: keepalive` comment every
    15 s. The client refetches what it shows when the version moves."""
    last, last_sent = None, time.monotonic()
    yield "retry: 3000\n\n"
    while should_continue():
        version = views.overview_version(layout)
        now = time.monotonic()
        if version != last:
            last, last_sent = version, now
            yield f"event: version\ndata: {json.dumps({'version': version})}\n\n"
        elif now - last_sent > KEEPALIVE_S:
            last_sent = now
            yield ": keepalive\n\n"
        time.sleep(STREAM_POLL_S)


def answer_approval(layout: StateLayout, approval_id: str, body: ApprovalAnswerIn) -> ApprovalDTO:
    """409 conflict if the approval is no longer pending."""
    decision = body.get("decision") if isinstance(body, dict) else None
    note = body.get("note", "") if isinstance(body, dict) else ""
    if decision not in ("approve", "reject") or not isinstance(note, str):
        raise ApiError(400, "bad_request", "body must be {decision: approve|reject, note}")
    store = FileApprovalStore(layout)
    if store.get(approval_id) is None:
        raise ApiError(404, "not_found", f"no approval {approval_id}")
    status = ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
    try:
        return _approval(store.answer(approval_id, status, f"dashboard: {note[:200]}"))
    except ApprovalError as err:
        raise ApiError(409, "conflict", str(err)) from None


def set_takeover(layout: StateLayout, worker_id: str, body: TakeoverIn) -> WorkerDTO:
    on = body.get("on") if isinstance(body, dict) else None
    if not isinstance(on, bool):
        raise ApiError(400, "bad_request", "body must be {on: true|false}")
    if read_worker(layout, WorkerId(worker_id)) is None:
        raise ApiError(404, "not_found", f"no worker {worker_id}")
    mark_takeover(layout, WorkerId(worker_id), on, "toggled from the dashboard")
    record = read_worker(layout, WorkerId(worker_id))
    assert record is not None
    return views.worker_row(record, [])


def request_stop(layout: StateLayout) -> OverviewDTO:
    layout.stop_flag.write_text(
        f"requested from the dashboard at {time.time()}\n", encoding="utf-8"
    )
    return get_overview(layout)
