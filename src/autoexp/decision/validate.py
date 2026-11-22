"""Validate untrusted decisions (from the LLM) before they have any effect."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from autoexp.config.schema import ParamKind, ParamSpace
from autoexp.core.ids import RunId
from autoexp.core.models import (
    CodeChangeProposal,
    Decision,
    DecisionAction,
    ParamValue,
    ProposedRun,
    RunPurpose,
)
from autoexp.decision.base import DecisionContext, config_key

MAX_TEXT = 2000
MAX_DIFF = 20000


class InvalidDecision(ValueError):
    pass


def clamp_value(value: Any, space: ParamSpace) -> ParamValue:
    if space.kind is ParamKind.CHOICE:
        if value in space.values:
            return value  # type: ignore[no-any-return]
        raise InvalidDecision(f"{space.name}={value!r} is not one of {list(space.values)}")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidDecision(f"{space.name}={value!r} is not a finite number")
    assert space.low is not None and space.high is not None
    clamped = min(max(float(value), space.low), space.high)
    return int(round(clamped)) if space.kind is ParamKind.INT else clamped


def clamp_config(
    config: Mapping[str, Any], space: Mapping[str, ParamSpace]
) -> dict[str, ParamValue]:
    """Reject unknown parameters, clamp numbers into bounds; missing ones are an error."""
    if not isinstance(config, Mapping):
        raise InvalidDecision("config must be an object")
    unknown = set(config) - set(space)
    if unknown:
        raise InvalidDecision(f"unknown parameters {sorted(unknown)}")
    missing = set(space) - set(config)
    if missing:
        raise InvalidDecision(f"missing parameters {sorted(missing)}")
    return {name: clamp_value(config[name], space[name]) for name in sorted(space)}


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def parse_exploration(
    raw: Mapping[str, Any], context: DecisionContext, engine: str, seeds: list[int]
) -> Decision:
    """Turn a decoded JSON reply into a LAUNCH Decision or raise InvalidDecision.

    Unknown keys and parameters are rejected, values are clamped into the
    search space, already-tried configs and proposals beyond `free_slots` are
    dropped, and seeds come from `seeds` (the caller's unused seeds), never from
    the reply.
    """
    if not isinstance(raw, Mapping):
        raise InvalidDecision("reply must be an object")
    allowed = {"proposals", "rationale", "code_changes", "action", "narrative"}
    unknown = set(raw) - allowed
    if unknown:
        raise InvalidDecision(f"unknown keys {sorted(unknown)}")
    proposals_raw = raw.get("proposals")
    if not isinstance(proposals_raw, list) or not proposals_raw:
        raise InvalidDecision("proposals must be a non-empty list")
    tried = {config_key(r.spec.config) for r in context.runs}
    known_runs = {r.spec.run_id for r in context.runs}
    proposals: list[ProposedRun] = []
    for item in proposals_raw:
        if len(proposals) >= context.free_slots:
            break
        if not isinstance(item, Mapping):
            raise InvalidDecision("each proposal must be an object")
        config = clamp_config(item.get("config", {}), context.search_space)
        key = config_key(config)
        if key in tried:
            continue
        tried.add(key)
        parent = item.get("parent_run_id")
        parent_id = RunId(parent) if isinstance(parent, str) and parent in known_runs else None
        proposals.append(
            ProposedRun(
                config=config,
                seed=seeds[len(proposals) % len(seeds)],
                purpose=RunPurpose.FORK if parent_id else RunPurpose.EXPLORE,
                parent_run_id=parent_id,
                rationale=_text(item.get("rationale"), 500),
            )
        )
    if not proposals:
        raise InvalidDecision("every proposal was a duplicate of a tried config")
    code_changes = []
    for change in raw.get("code_changes") or []:
        if isinstance(change, Mapping) and _text(change.get("summary")):
            code_changes.append(
                CodeChangeProposal(
                    summary=_text(change.get("summary"), 300),
                    diff=_text(change.get("diff"), MAX_DIFF),
                )
            )
    return Decision(
        round_index=context.round_index,
        action=DecisionAction.LAUNCH,
        proposals=tuple(proposals),
        rationale=_text(raw.get("rationale")) or "LLM proposal",
        engine=engine,
        code_changes=tuple(code_changes),
    )
