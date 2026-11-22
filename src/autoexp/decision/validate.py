"""Validate untrusted decisions (from the LLM) before they have any effect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autoexp.config.schema import ParamSpace
from autoexp.core.models import Decision, ParamValue
from autoexp.decision.base import DecisionContext


class InvalidDecision(ValueError):
    pass


def parse_decision(raw: Mapping[str, Any], context: DecisionContext, engine: str) -> Decision:
    """Turn a decoded JSON reply into a Decision or raise InvalidDecision.

    Unknown keys and parameters are rejected, values are clamped into the
    search space, proposals beyond `free_slots` are dropped, and seeds must be
    non-negative integers.
    """
    raise NotImplementedError


def clamp_config(
    config: Mapping[str, Any], space: Mapping[str, ParamSpace]
) -> dict[str, ParamValue]:
    raise NotImplementedError
