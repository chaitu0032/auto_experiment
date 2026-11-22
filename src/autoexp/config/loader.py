"""Load and validate a TOML experiment config.

Validation fails fast with a message naming the bad key, and checks cross-field
rules: initial configs lie inside the search space, `workers.count *
workers.runs_per_worker <= devices.max_concurrent_runs <= len(devices.slots)`,
halving rungs are increasing fractions in (0, 1), budgets are positive, the
dashboard host is a loopback address.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autoexp.config.schema import (
    BudgetConfig,
    DashboardConfig,
    DevicesConfig,
    DivergenceConfig,
    EngineKind,
    ExperimentConfig,
    GoalConfig,
    HalvingConfig,
    HalvingMode,
    HerdrConfig,
    LLMConfig,
    LLMJudgeConfig,
    MasterConfig,
    ParamKind,
    ParamSpace,
    PlateauConfig,
    PruningConfig,
    StopConfig,
    TrainerConfig,
    WorkerRuntimeKind,
    WorkersConfig,
)
from autoexp.core.ids import validate_device_id
from autoexp.core.models import ParamValue

KNOWN_PRUNERS = ("divergence", "plateau", "halving", "llm_judge")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class ConfigError(ValueError):
    """Raised with a user-facing message when a config is invalid."""


def _table(data: Mapping[str, Any], key: str, where: str = "") -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"missing table [{where}{key}]")
    return value


def _get(data: Mapping[str, Any], key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing key {where}.{key}")
    value = data[key]
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if float in kinds and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if isinstance(value, bool) and bool not in kinds:
        raise ConfigError(f"{where}.{key} must be {kinds[0].__name__}, got a boolean")
    if not isinstance(value, kinds):
        raise ConfigError(f"{where}.{key} must be {kinds[0].__name__}, got {value!r}")
    return value


def _positive(value: float, name: str) -> None:
    if value <= 0:
        raise ConfigError(f"{name} must be > 0, got {value}")


def _str_list(data: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = _get(data, key, list, where)
    if not value or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where}.{key} must be a non-empty list of strings")
    return tuple(value)


def _param_space(name: str, raw: Any) -> ParamSpace:
    where = f"search_space.{name}"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} must be a table")
    try:
        kind = ParamKind(raw.get("kind"))
    except ValueError:
        raise ConfigError(f"{where}.kind must be one of {[k.value for k in ParamKind]}") from None
    if kind is ParamKind.CHOICE:
        values = raw.get("values")
        if not isinstance(values, list) or not values:
            raise ConfigError(f"{where}.values must be a non-empty list")
        return ParamSpace(name=name, kind=kind, values=tuple(values))
    low = _get(raw, "low", float, where)
    high = _get(raw, "high", float, where)
    if low >= high:
        raise ConfigError(f"{where}: low must be < high")
    if kind is ParamKind.LOG_FLOAT and low <= 0:
        raise ConfigError(f"{where}: log_float bounds must be > 0")
    return ParamSpace(name=name, kind=kind, low=low, high=high)


def in_space(value: ParamValue, space: ParamSpace) -> bool:
    if space.kind is ParamKind.CHOICE:
        return value in space.values
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if space.kind is ParamKind.INT and not isinstance(value, int):
        return False
    assert space.low is not None and space.high is not None
    return space.low <= value <= space.high


def _initial_configs(
    raw: Any, space: Mapping[str, ParamSpace]
) -> tuple[Mapping[str, ParamValue], ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("at least one [[initial_configs]] entry is required")
    configs = []
    for index, entry in enumerate(raw):
        where = f"initial_configs[{index}]"
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{where} must be a table")
        unknown = set(entry) - set(space)
        if unknown:
            raise ConfigError(f"{where}: unknown parameters {sorted(unknown)}")
        for name, value in entry.items():
            if not in_space(value, space[name]):
                raise ConfigError(f"{where}.{name}={value!r} is outside the search space")
        configs.append(dict(entry))
    return tuple(configs)


def parse_config(data: Mapping[str, Any]) -> ExperimentConfig:
    experiment_id = _get(data, "experiment_id", str, "")
    trainer_t = _table(data, "trainer")
    trainer = TrainerConfig(
        name=_get(trainer_t, "name", str, "trainer"),
        command=_str_list(trainer_t, "command", "trainer"),
        total_steps=_get(trainer_t, "total_steps", int, "trainer"),
    )
    _positive(trainer.total_steps, "trainer.total_steps")

    goal_t = _table(data, "goal")
    goal = GoalConfig(
        metric=_get(goal_t, "metric", str, "goal"),
        window=_get(goal_t, "window", int, "goal"),
        threshold=_get(goal_t, "threshold", float, "goal"),
        seeds_required=_get(goal_t, "seeds_required", int, "goal"),
    )
    if goal.metric != "rolling_return":
        raise ConfigError("goal.metric must be 'rolling_return'")
    _positive(goal.window, "goal.window")
    _positive(goal.seeds_required, "goal.seeds_required")

    space_t = _table(data, "search_space")
    space = {name: _param_space(name, raw) for name, raw in space_t.items()}
    if not space:
        raise ConfigError("search_space must define at least one parameter")
    initial = _initial_configs(data.get("initial_configs"), space)

    pruning = _parse_pruning(_table(data, "pruning"))
    budget = _parse_budget(_table(data, "budget"))
    stop_t = _table(data, "stop")
    stop = StopConfig(
        no_improvement_rounds=_get(stop_t, "no_improvement_rounds", int, "stop"),
        crash_limit=_get(stop_t, "crash_limit", int, "stop"),
        max_rounds=_get(stop_t, "max_rounds", int, "stop"),
        stop_on_llm_budget=bool(stop_t.get("stop_on_llm_budget", True)),
        min_improvement=float(stop_t.get("min_improvement", 1.0)),
    )
    for name in ("no_improvement_rounds", "crash_limit", "max_rounds"):
        _positive(getattr(stop, name), f"stop.{name}")

    workers, devices = _parse_capacity(_table(data, "workers"), _table(data, "devices"))
    herdr_t = _table(data, "herdr")
    session = herdr_t.get("session")
    if session is not None and not isinstance(session, str):
        raise ConfigError("herdr.session must be a string")
    herdr = HerdrConfig(
        command=_str_list(herdr_t, "command", "herdr"),
        session=session or None,
        workspace_label=_get(herdr_t, "workspace_label", str, "herdr"),
    )
    llm_t = _table(data, "llm")
    try:
        engine = EngineKind(_get(llm_t, "engine", str, "llm"))
    except ValueError:
        raise ConfigError("llm.engine must be 'llm' or 'deterministic'") from None
    llm = LLMConfig(
        engine=engine,
        command=_str_list(llm_t, "command", "llm"),
        timeout_s=_get(llm_t, "timeout_s", float, "llm"),
    )
    master = MasterConfig(tick_s=_get(_table(data, "master"), "tick_s", float, "master"))
    _positive(master.tick_s, "master.tick_s")
    dash_t = _table(data, "dashboard")
    dashboard = DashboardConfig(
        host=_get(dash_t, "host", str, "dashboard"), port=_get(dash_t, "port", int, "dashboard")
    )
    if dashboard.host not in LOOPBACK_HOSTS:
        raise ConfigError("dashboard.host must be a loopback address")
    if not 0 < dashboard.port < 65536:
        raise ConfigError("dashboard.port must be in 1..65535")
    return ExperimentConfig(
        experiment_id=experiment_id,
        trainer=trainer,
        goal=goal,
        search_space=space,
        initial_configs=initial,
        pruning=pruning,
        budget=budget,
        stop=stop,
        workers=workers,
        devices=devices,
        herdr=herdr,
        llm=llm,
        master=master,
        dashboard=dashboard,
    )


def _parse_pruning(t: Mapping[str, Any]) -> PruningConfig:
    order = _str_list(t, "order", "pruning")
    unknown = [p for p in order if p not in KNOWN_PRUNERS]
    if unknown:
        raise ConfigError(f"pruning.order: unknown pruners {unknown}")
    div_t = _table(t, "divergence", "pruning.")
    plat_t = _table(t, "plateau", "pruning.")
    half_t = _table(t, "halving", "pruning.")
    judge_t = _table(t, "llm_judge", "pruning.")
    try:
        mode = HalvingMode(_get(half_t, "mode", str, "pruning.halving"))
    except ValueError:
        raise ConfigError("pruning.halving.mode must be 'sync' or 'async'") from None
    rungs_raw = _get(half_t, "rungs", list, "pruning.halving")
    rungs = tuple(float(r) for r in rungs_raw)
    if not rungs or any(not 0 < r < 1 for r in rungs) or list(rungs) != sorted(set(rungs)):
        raise ConfigError("pruning.halving.rungs must be increasing fractions in (0, 1)")
    halving = HalvingConfig(
        mode=mode,
        rungs=rungs,
        eta=_get(half_t, "eta", int, "pruning.halving"),
        min_peers=_get(half_t, "min_peers", int, "pruning.halving"),
    )
    if halving.eta < 2:
        raise ConfigError("pruning.halving.eta must be >= 2")
    return PruningConfig(
        order=order,
        divergence=DivergenceConfig(
            kl_max=_get(div_t, "kl_max", float, "pruning.divergence"),
            kl_patience=_get(div_t, "kl_patience", int, "pruning.divergence"),
            value_loss_max=_get(div_t, "value_loss_max", float, "pruning.divergence"),
        ),
        plateau=PlateauConfig(
            min_steps=_get(plat_t, "min_steps", int, "pruning.plateau"),
            patience_steps=_get(plat_t, "patience_steps", int, "pruning.plateau"),
            min_delta=_get(plat_t, "min_delta", float, "pruning.plateau"),
        ),
        halving=halving,
        llm_judge=LLMJudgeConfig(
            enabled=_get(judge_t, "enabled", bool, "pruning.llm_judge"),
            every_ticks=_get(judge_t, "every_ticks", int, "pruning.llm_judge"),
        ),
    )


def _parse_budget(t: Mapping[str, Any]) -> BudgetConfig:
    budget = BudgetConfig(
        wall_clock_s=_get(t, "wall_clock_s", float, "budget"),
        compute_s=_get(t, "compute_s", float, "budget"),
        env_steps=_get(t, "env_steps", int, "budget"),
        llm_calls=_get(t, "llm_calls", int, "budget"),
        llm_cost_usd=_get(t, "llm_cost_usd", float, "budget"),
    )
    for name in ("wall_clock_s", "compute_s", "env_steps"):
        _positive(getattr(budget, name), f"budget.{name}")
    if budget.llm_calls < 0 or budget.llm_cost_usd < 0:
        raise ConfigError("LLM budgets must be >= 0")
    return budget


def _parse_capacity(
    w: Mapping[str, Any], d: Mapping[str, Any]
) -> tuple[WorkersConfig, DevicesConfig]:
    try:
        runtime = WorkerRuntimeKind(_get(w, "runtime", str, "workers"))
    except ValueError:
        raise ConfigError("workers.runtime must be 'claude' or 'shell'") from None
    workers = WorkersConfig(
        count=_get(w, "count", int, "workers"),
        runs_per_worker=_get(w, "runs_per_worker", int, "workers"),
        runtime=runtime,
        agent_command=_str_list(w, "agent_command", "workers"),
    )
    slots = _str_list(d, "slots", "devices")
    for slot in slots:
        try:
            validate_device_id(slot)
        except ValueError as err:
            raise ConfigError(f"devices.slots: {err}") from None
    if len(set(slots)) != len(slots):
        raise ConfigError("devices.slots must be unique")
    devices = DevicesConfig(
        slots=slots,
        threads_per_slot=_get(d, "threads_per_slot", int, "devices"),
        max_concurrent_runs=_get(d, "max_concurrent_runs", int, "devices"),
    )
    _positive(workers.count, "workers.count")
    _positive(workers.runs_per_worker, "workers.runs_per_worker")
    _positive(devices.threads_per_slot, "devices.threads_per_slot")
    if devices.max_concurrent_runs > len(devices.slots):
        raise ConfigError("devices.max_concurrent_runs cannot exceed the number of device slots")
    if workers.count * workers.runs_per_worker > devices.max_concurrent_runs:
        raise ConfigError(
            "workers.count * workers.runs_per_worker exceeds devices.max_concurrent_runs"
        )
    return workers, devices


def load_config(path: Path) -> ExperimentConfig:
    """Parse `path` with tomllib and return a validated ExperimentConfig."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path}: invalid TOML: {err}") from None
    return parse_config(data)
