"""Data models shared by every component.

All models are frozen dataclasses: components build new values instead of
mutating shared ones. Every record that is written to disk is plain JSON-able
data (strings, numbers, booleans, lists, dicts, None) plus the enums below,
whose values are their JSON form.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from autoexp.core.ids import DeviceId, RunId, WorkerId

ParamValue = int | float | str | bool
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


# --------------------------------------------------------------------------- runs


class RunStatus(StrEnum):
    """Lifecycle of a run, derived by folding its events (never stored alone)."""

    ALLOCATED = "allocated"  # master gave it a run ID and spec
    ASSIGNED = "assigned"  # assign message is in a worker inbox
    RUNNING = "running"  # supervisor holds its locks and the trainer started
    SOLVED = "solved"  # goal reached; stopped to free the device
    COMPLETED = "completed"  # trainer exited 0 after its full budget
    PRUNED = "pruned"  # stopped on a prune verdict
    STOPPED = "stopped"  # stopped for another reason (user, budget, stop rule)
    CRASHED = "crashed"  # trainer exited non-zero or with a traceback
    LOST = "lost"  # non-terminal on disk but its run lock is free

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.SOLVED,
        RunStatus.COMPLETED,
        RunStatus.PRUNED,
        RunStatus.STOPPED,
        RunStatus.CRASHED,
        RunStatus.LOST,
    }
)


class RunPurpose(StrEnum):
    EXPLORE = "explore"  # a new configuration
    FORK = "fork"  # a mutation of a parent run's configuration
    SEED_CHECK = "seed_check"  # same configuration, another seed
    RETRY = "retry"  # re-run of a lost or crashed run


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Immutable description of one experiment; written once to spec.json."""

    run_id: RunId
    experiment_id: str
    round_index: int
    trainer: str
    config: Mapping[str, ParamValue]
    seed: int
    total_steps: int
    purpose: RunPurpose
    parent_run_id: RunId | None = None
    rationale: str = ""


class RunEventKind(StrEnum):
    ALLOCATED = "allocated"
    ASSIGNED = "assigned"
    LAUNCHED = "launched"  # supervisor took run + device locks
    STARTED = "started"  # trainer process is up
    HEARTBEAT = "heartbeat"
    STOP_REQUESTED = "stop_requested"
    EXITED = "exited"  # trainer exit; data carries exit code and outcome
    LOST = "lost"


class EventSource(StrEnum):
    MASTER = "master"
    WORKER = "worker"
    SUPERVISOR = "supervisor"
    HUMAN = "human"


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One line of runs/<run_id>/events.jsonl."""

    run_id: RunId
    at: float
    kind: RunEventKind
    source: EventSource
    data: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Current view of a run, produced by folding RunSpec + RunEvents."""

    spec: RunSpec
    status: RunStatus
    worker_id: WorkerId | None
    device_id: DeviceId | None
    allocated_at: float
    started_at: float | None = None
    ended_at: float | None = None
    exit_code: int | None = None
    stop_reason: str | None = None
    crash_count: int = 0


# ---------------------------------------------------------------------- telemetry


class TelemetryKind(StrEnum):
    EPISODE = "episode"
    UPDATE = "update"
    EVAL = "eval"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """One line of runs/<run_id>/metrics.jsonl, written by the trainer.

    `values` holds the kind-specific fields listed in docs/architecture.md 3.4.
    """

    run_id: RunId
    t: float
    step: int
    kind: TelemetryKind
    values: Mapping[str, float | int | str | None]


@dataclass(frozen=True, slots=True)
class CurveSummary:
    """Observability summary of one run's telemetry so far."""

    run_id: RunId
    step: int
    episodes: int
    rolling_return: float | None
    best_rolling_return: float | None
    best_at_step: int | None
    last_eval_return: float | None
    approx_kl: float | None
    entropy: float | None
    value_loss: float | None
    grad_norm: float | None
    learning_rate: float | None
    sps: float | None
    rss_mb: float | None
    nan_seen: bool
    kl_over_limit_streak: int
    malformed_lines: int
    curve: tuple[tuple[int, float], ...] = ()  # (step, rolling_return) points, thinned
    recent_returns: tuple[float, ...] = ()  # last `window` episode returns (fold state)
    value_loss_peak: float | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    """Everything the master knows about a live run on one tick."""

    record: RunRecord
    summary: CurveSummary | None
    traceback: str | None
    pane_excerpt: str | None


# ------------------------------------------------------------------------ pruning


class PruneAction(StrEnum):
    KEEP = "keep"
    PRUNE = "prune"
    SOLVED = "solved"  # stop because the goal is reached, not because it is weak


@dataclass(frozen=True, slots=True)
class PruneVerdict:
    run_id: RunId
    pruner: str
    action: PruneAction
    reason: str
    evidence: Mapping[str, JsonValue] = field(default_factory=dict)


# ----------------------------------------------------------------------- decision


class DecisionAction(StrEnum):
    LAUNCH = "launch"  # start the proposed runs
    VERIFY_SEEDS = "verify_seeds"  # re-run a candidate winner on more seeds
    WAIT = "wait"  # nothing to do until running runs settle
    STOP = "stop"  # end the experiment


@dataclass(frozen=True, slots=True)
class ProposedRun:
    config: Mapping[str, ParamValue]
    seed: int
    purpose: RunPurpose
    parent_run_id: RunId | None = None
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class CodeChangeProposal:
    """A training-code change the decision engine wants; always needs approval."""

    summary: str
    diff: str


@dataclass(frozen=True, slots=True)
class Decision:
    round_index: int
    action: DecisionAction
    proposals: tuple[ProposedRun, ...]
    rationale: str
    engine: str  # "llm" or "deterministic"
    fallback_reason: str | None = None
    code_changes: tuple[CodeChangeProposal, ...] = ()
    narrative: str | None = None  # finding text when action is STOP


# ------------------------------------------------------------------ budgets / stop


@dataclass(frozen=True, slots=True)
class BudgetLine:
    name: str  # wall_clock_s, compute_s, env_steps, llm_calls, llm_cost_usd
    used: float
    limit: float

    @property
    def fraction(self) -> float:
        return self.used / self.limit if self.limit > 0 else 0.0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    at: float
    lines: tuple[BudgetLine, ...]


@dataclass(frozen=True, slots=True)
class StopVerdict:
    should_stop: bool
    rule: str | None
    reason: str


# -------------------------------------------------------------------------- locks


class LockKind(StrEnum):
    MASTER = "master"
    RUN = "run"
    DEVICE = "device"


@dataclass(frozen=True, slots=True)
class LockInfo:
    """Sidecar metadata of a held lock (locks/<name>.json), for display only."""

    name: str
    kind: LockKind
    holder_pid: int
    host: str
    owner: str  # "master", "supervisor:r0003", ...
    acquired_at: float


# ------------------------------------------------------------- steering / workers


class InboxKind(StrEnum):
    ASSIGN = "assign"  # body: run_id, device_id
    STOP = "stop"  # body: run_id, reason
    DEBUG = "debug"  # body: run_id, evidence; asks the agent to investigate
    APPROVAL_RESULT = "approval_result"
    NOTE = "note"


@dataclass(frozen=True, slots=True)
class InboxMessage:
    """One durable steering message: workers/<id>/inbox/<seq>-<msg_id>.json."""

    msg_id: str  # deterministic, e.g. "r0003-assign", so replays are idempotent
    seq: int
    worker_id: WorkerId
    at: float
    kind: InboxKind
    body: Mapping[str, JsonValue]


class WorkerState(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    TAKEOVER = "takeover"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    """One line of workers/<id>/events.jsonl."""

    worker_id: WorkerId
    at: float
    state: WorkerState
    run_id: RunId | None = None
    note: str = ""
    verdict: str | None = None  # after a DEBUG request: "retry" or "unfixable"


@dataclass(frozen=True, slots=True)
class WorkerRecord:
    worker_id: WorkerId
    pane_id: str | None
    runtime: str
    state: WorkerState
    takeover: bool
    run_ids: tuple[RunId, ...]


# ---------------------------------------------------------------------- approvals


class ApprovalKind(StrEnum):
    CODE_CHANGE = "code_change"
    BUDGET_EXTENSION = "budget_extension"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: str
    kind: ApprovalKind
    summary: str
    payload: Mapping[str, JsonValue]
    requested_by: str
    at: float
    status: ApprovalStatus = ApprovalStatus.PENDING


# ------------------------------------------------------------------ master events


class MasterEventKind(StrEnum):
    STARTED = "started"
    RECOVERED = "recovered"
    ROUND_STARTED = "round_started"
    RUN_ALLOCATED = "run_allocated"
    PRUNE = "prune"
    DECISION = "decision"
    APPROVAL_REQUESTED = "approval_requested"
    BUDGET = "budget"
    STEER_HELD = "steer_held"  # takeover or busy pane
    STEERED = "steered"  # a message was delivered to a worker
    WORKER_REPORT = "worker_report"  # a worker's status line, relayed
    LLM_CALL = "llm_call"  # one metered LLM call (purpose, cost, tokens, ok)
    RUN_FINISHED = "run_finished"
    STOPPED = "stopped"
    FINDING = "finding"


@dataclass(frozen=True, slots=True)
class MasterEvent:
    """One line of master/events.jsonl; the dashboard's event feed."""

    at: float
    kind: MasterEventKind
    summary: str  # one human-readable line, also printed as the digest
    data: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Finding:
    """The written result shown on the dashboard when the loop ends."""

    stop_rule: str
    winner_config: Mapping[str, ParamValue] | None
    winner_run_ids: tuple[RunId, ...]
    tried: int
    text: str  # markdown: what was tried, what worked, why
