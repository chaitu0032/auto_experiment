"""JSON response shapes of the dashboard API (the frontend's contract).

Every response uses one envelope: `{"ok": bool, "data": <shape> | null,
"error": {"code": str, "message": str} | null}`. Shapes are TypedDicts so the
backend is type-checked against the same names the JS modules use
(`web/js/api.js` documents the mirror in JSDoc).
"""

from __future__ import annotations

from typing import Generic, Literal, TypedDict, TypeVar

T = TypeVar("T")


class ErrorDTO(TypedDict):
    code: str  # "not_found", "bad_request", "forbidden_origin", "conflict", "internal"
    message: str


class Envelope(TypedDict, Generic[T]):
    ok: bool
    data: T | None
    error: ErrorDTO | None


class RunRowDTO(TypedDict):
    run_id: str
    round_index: int
    purpose: str
    parent_run_id: str | None
    status: str  # RunStatus value
    worker_id: str | None
    device_id: str | None
    seed: int
    config: dict[str, int | float | str | bool]
    step: int
    total_steps: int
    rolling_return: float | None
    best_rolling_return: float | None
    sps: float | None
    stop_reason: str | None
    started_at: float | None
    ended_at: float | None


class CurvePointDTO(TypedDict):
    step: int
    rolling_return: float | None
    episode_return: float | None


class LossPointDTO(TypedDict):
    step: int
    policy_loss: float | None
    value_loss: float | None
    entropy: float | None
    approx_kl: float | None


class CurveDTO(TypedDict):
    run_id: str
    returns: list[CurvePointDTO]
    losses: list[LossPointDTO]
    next_step: int  # pass back as ?since_step= for incremental fetches


class RunEventDTO(TypedDict):
    at: float
    kind: str
    source: str
    data: dict[str, object]


class RunDetailDTO(TypedDict):
    run: RunRowDTO
    events: list[RunEventDTO]
    stdout_tail: str


class MasterEventDTO(TypedDict):
    seq: int  # line number in master/events.jsonl, monotonic
    at: float
    kind: str  # MasterEventKind value
    summary: str
    data: dict[str, object]  # prune: run_id, pruner, reason, evidence; decision: engine, ...


class EventsDTO(TypedDict):
    events: list[MasterEventDTO]
    next_seq: int


class BudgetLineDTO(TypedDict):
    name: str
    used: float
    limit: float
    fraction: float


class BudgetDTO(TypedDict):
    at: float
    lines: list[BudgetLineDTO]


class ApprovalDTO(TypedDict):
    approval_id: str
    kind: str
    summary: str
    payload: dict[str, object]
    requested_by: str
    at: float
    status: str


class WorkerDTO(TypedDict):
    worker_id: str
    pane_id: str | None
    runtime: str
    state: str
    takeover: bool
    run_ids: list[str]


class PaneDTO(TypedDict):
    worker_id: str
    pane_id: str | None
    text: str  # tail cached by the master's observer each tick
    read_at: float | None


class FindingDTO(TypedDict):
    stop_rule: str
    winner_config: dict[str, int | float | str | bool] | None
    winner_run_ids: list[str]
    tried: int
    text: str  # markdown, rendered as plain text by the UI


class GoalDTO(TypedDict):
    metric: str
    window: int
    threshold: float
    seeds_required: int


class OverviewDTO(TypedDict):
    experiment_id: str
    goal: GoalDTO
    version: int  # changes whenever any state file the UI shows changes
    round_index: int
    master: Literal["running", "stopped", "absent"]
    stop_requested: bool
    runs: list[RunRowDTO]
    workers: list[WorkerDTO]
    budget: BudgetDTO | None
    approvals: list[ApprovalDTO]
    finding: FindingDTO | None


class ApprovalAnswerIn(TypedDict):
    decision: Literal["approve", "reject"]
    note: str


class TakeoverIn(TypedDict):
    on: bool
