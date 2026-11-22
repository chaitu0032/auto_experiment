"""Deterministic decision engine; also the fallback for the LLM engine.

Order of rules:
1. A config holds the goal on `seeds_required` seeds -> STOP (goal reached).
2. A config solved on fewer seeds -> VERIFY_SEEDS with unused seeds.
3. Otherwise -> LAUNCH mutations of the top-k configs (scaled learning rate,
   neighbouring choices, nudged coefficients) inside the search space, skipping
   configs already tried, up to `free_slots` proposals.
Rules 1 and 2 are the verification protocol shared by every engine
(`protocol_decision`); engines differ only in rule 3.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

from autoexp.config.schema import ParamKind, ParamSpace
from autoexp.core.models import (
    Decision,
    DecisionAction,
    ParamValue,
    ProposedRun,
    RunPurpose,
)
from autoexp.decision.base import (
    ConfigStanding,
    DecisionContext,
    config_key,
    standings,
    winner,
)
from autoexp.decision.validate import clamp_value

SEED_POOL = tuple(range(1, 1000))
EXPLORATION_SEED = 1  # configs are compared on one seed; other seeds verify a winner


def unused_seeds(context: DecisionContext, count: int, config: Mapping | None = None) -> list[int]:
    """Seeds not yet used (for `config` if given, else by any run), lowest first."""
    if config is not None:
        key = config_key(config)
        used = {r.spec.seed for r in context.runs if config_key(r.spec.config) == key}
    else:
        used = set()
    return [s for s in SEED_POOL if s not in used][: max(count, 1)]


def protocol_decision(context: DecisionContext, engine: str) -> Decision | None:
    """Rules 1 and 2: stop on a verified winner, else verify a candidate."""
    found = winner(context)
    if found is not None:
        return Decision(
            round_index=context.round_index,
            action=DecisionAction.STOP,
            proposals=(),
            rationale=(
                f"config holds the goal on {len(found.solved_seeds)} seeds "
                f"{sorted(found.solved_seeds)} (runs {', '.join(found.run_ids)})"
            ),
            engine=engine,
        )
    if context.free_slots <= 0:
        return None
    for standing in standings(context):
        if not standing.solved_seeds or standing.refuted:
            continue
        need = context.goal.seeds_required - len(standing.solved_seeds) - standing.live
        if need <= 0:
            continue
        count = min(need, context.free_slots)
        seeds = unused_seeds(context, count, standing.config)
        proposals = tuple(
            ProposedRun(
                config=dict(standing.config),
                seed=seed,
                purpose=RunPurpose.SEED_CHECK,
                parent_run_id=standing.first_solver,
                rationale=f"verify {standing.first_solver} on seed {seed}",
            )
            for seed in seeds[:count]
        )
        return Decision(
            round_index=context.round_index,
            action=DecisionAction.VERIFY_SEEDS,
            proposals=proposals,
            rationale=(
                f"{standing.first_solver} reached the goal on seeds "
                f"{sorted(standing.solved_seeds)}; it must hold on "
                f"{context.goal.seeds_required} seeds before it is called the winner"
            ),
            engine=engine,
        )
    return None


def mutate(
    config: Mapping[str, ParamValue], space: Mapping[str, ParamSpace], rng: random.Random
) -> dict[str, ParamValue]:
    """Change one or two parameters of `config` within the search space."""
    names = sorted(space)
    chosen = rng.sample(names, k=min(len(names), rng.choice((1, 2))))
    result = dict(config)
    for name in chosen:
        result[name] = _perturb(result.get(name), space[name], rng)
    return result


def _perturb(value: ParamValue | None, space: ParamSpace, rng: random.Random) -> ParamValue:
    if space.kind is ParamKind.CHOICE:
        values = list(space.values)
        if value in values:
            i = values.index(value)
            neighbours = [values[j] for j in (i - 1, i + 1) if 0 <= j < len(values)]
            return rng.choice(neighbours or values)
        return rng.choice(values)
    assert space.low is not None and space.high is not None
    base = float(value) if isinstance(value, int | float) else (space.low + space.high) / 2
    if space.kind is ParamKind.LOG_FLOAT:
        factor = rng.choice((0.5, 0.7, 1.4, 2.0))
        return clamp_value(base * factor, space)
    if space.kind is ParamKind.INT:
        step = max(1, round((space.high - space.low) * 0.25))
        return clamp_value(int(base) + rng.choice((-step, step)), space)
    span = space.high - space.low
    return clamp_value(base + rng.choice((-0.25, 0.25)) * span, space)


def explore(context: DecisionContext, top_k: int, engine: str) -> Decision:
    """Rule 3: mutate the best configs into new, untried ones."""
    if context.free_slots <= 0:
        return Decision(context.round_index, DecisionAction.WAIT, (), "no free slots", engine)
    ranked = [s for s in standings(context) if not s.refuted and math.isfinite(s.best)]
    parents: list[ConfigStanding] = ranked[:top_k] or list(standings(context))[:top_k]
    if not parents:
        return Decision(context.round_index, DecisionAction.WAIT, (), "nothing to build on", engine)
    tried = {config_key(r.spec.config) for r in context.runs}
    rng = random.Random(context.round_index * 7919 + len(context.runs))
    proposals: list[ProposedRun] = []
    attempts = 0
    while len(proposals) < context.free_slots and attempts < 200:
        attempts += 1
        parent = parents[len(proposals) % len(parents)]
        child = mutate(parent.config, context.search_space, rng)
        key = config_key(child)
        if key in tried:
            continue
        tried.add(key)
        changed = sorted(k for k in child if child[k] != parent.config.get(k))
        proposals.append(
            ProposedRun(
                config=child,
                seed=EXPLORATION_SEED,
                purpose=RunPurpose.FORK,
                parent_run_id=parent.run_ids[0],
                rationale=(
                    f"fork of {parent.run_ids[0]} (best {parent.best:.1f}) changing "
                    + ", ".join(
                        f"{k}={child[k]:.4g}" if isinstance(child[k], float) else f"{k}={child[k]}"
                        for k in changed
                    )
                ),
            )
        )
    if not proposals:
        return Decision(
            context.round_index, DecisionAction.WAIT, (), "search space exhausted", engine
        )
    names = ", ".join(f"{p.run_ids[0]} ({p.best:.1f})" for p in parents)
    return Decision(
        round_index=context.round_index,
        action=DecisionAction.LAUNCH,
        proposals=tuple(proposals),
        rationale=f"no config solved yet; exploring around the best so far: {names}",
        engine=engine,
    )


class DeterministicDecisionEngine:
    name = "deterministic"

    def __init__(self, top_k: int = 2) -> None:
        self._top_k = top_k

    def decide(self, context: DecisionContext) -> Decision:
        return protocol_decision(context, self.name) or explore(context, self._top_k, self.name)

    def narrate(self, context: DecisionContext, stop_reason: str) -> str | None:
        return None
