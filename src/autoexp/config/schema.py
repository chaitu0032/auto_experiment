"""Configuration schema. One frozen dataclass per TOML table.

Every tunable number (budgets, thresholds, caps, ports) lives here and in
`configs/*.toml`; no component hard-codes them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from autoexp.core.models import ParamValue


class ParamKind(StrEnum):
    FLOAT = "float"
    LOG_FLOAT = "log_float"
    INT = "int"
    CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class ParamSpace:
    name: str
    kind: ParamKind
    low: float | None = None
    high: float | None = None
    values: tuple[ParamValue, ...] = ()


@dataclass(frozen=True, slots=True)
class TrainerConfig:
    name: str
    command: tuple[str, ...]
    total_steps: int


@dataclass(frozen=True, slots=True)
class GoalConfig:
    metric: str
    window: int
    threshold: float
    seeds_required: int


@dataclass(frozen=True, slots=True)
class DivergenceConfig:
    kl_max: float
    kl_patience: int
    value_loss_max: float


@dataclass(frozen=True, slots=True)
class PlateauConfig:
    min_steps: int
    patience_steps: int
    min_delta: float


class HalvingMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


@dataclass(frozen=True, slots=True)
class HalvingConfig:
    mode: HalvingMode
    rungs: tuple[float, ...]
    eta: int
    min_peers: int


@dataclass(frozen=True, slots=True)
class LLMJudgeConfig:
    enabled: bool
    every_ticks: int


@dataclass(frozen=True, slots=True)
class PruningConfig:
    order: tuple[str, ...]
    divergence: DivergenceConfig
    plateau: PlateauConfig
    halving: HalvingConfig
    llm_judge: LLMJudgeConfig


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    wall_clock_s: float
    compute_s: float
    env_steps: int
    llm_calls: int
    llm_cost_usd: float


@dataclass(frozen=True, slots=True)
class StopConfig:
    no_improvement_rounds: int
    crash_limit: int
    max_rounds: int


class WorkerRuntimeKind(StrEnum):
    CLAUDE = "claude"
    SHELL = "shell"


@dataclass(frozen=True, slots=True)
class WorkersConfig:
    count: int
    runs_per_worker: int
    runtime: WorkerRuntimeKind
    agent_command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DevicesConfig:
    slots: tuple[str, ...]
    threads_per_slot: int
    max_concurrent_runs: int


@dataclass(frozen=True, slots=True)
class HerdrConfig:
    command: tuple[str, ...]
    session: str | None
    workspace_label: str


class EngineKind(StrEnum):
    LLM = "llm"
    DETERMINISTIC = "deterministic"


@dataclass(frozen=True, slots=True)
class LLMConfig:
    engine: EngineKind
    command: tuple[str, ...]
    timeout_s: float


@dataclass(frozen=True, slots=True)
class MasterConfig:
    tick_s: float


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    experiment_id: str
    trainer: TrainerConfig
    goal: GoalConfig
    search_space: Mapping[str, ParamSpace]
    initial_configs: tuple[Mapping[str, ParamValue], ...]
    pruning: PruningConfig
    budget: BudgetConfig
    stop: StopConfig
    workers: WorkersConfig
    devices: DevicesConfig
    herdr: HerdrConfig
    llm: LLMConfig
    master: MasterConfig
    dashboard: DashboardConfig
