"""Encode and decode the frozen models in `core/models.py` to JSON-able dicts.

One place owns the wire format so every file (spec.json, events.jsonl,
inbox messages, approvals) round-trips the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

T = TypeVar("T")


def encode(model: object) -> dict[str, Any]:
    """Return the JSON-able dict for a model instance (enums by value)."""
    raise NotImplementedError


def decode(model_type: type[T], data: Mapping[str, Any]) -> T:
    """Build `model_type` from a dict, validating field types and enum values."""
    raise NotImplementedError
