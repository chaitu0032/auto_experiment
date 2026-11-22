"""Encode and decode the frozen models in `core/models.py` to JSON-able dicts.

One place owns the wire format so every file (spec.json, events.jsonl,
inbox messages, approvals) round-trips the same way.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Mapping
from enum import Enum
from functools import cache
from typing import Any, TypeVar, Union

T = TypeVar("T")


class CodecError(ValueError):
    pass


def encode(model: object) -> Any:
    """Return the JSON-able form of a model instance (enums by value)."""
    if dataclasses.is_dataclass(model) and not isinstance(model, type):
        return {f.name: encode(getattr(model, f.name)) for f in dataclasses.fields(model)}
    if isinstance(model, Enum):
        return model.value
    if isinstance(model, Mapping):
        return {str(k): encode(v) for k, v in model.items()}
    if isinstance(model, list | tuple):
        return [encode(v) for v in model]
    return model


@cache
def _hints(model_type: type) -> dict[str, Any]:
    return typing.get_type_hints(model_type)


def decode(model_type: type[T], data: Any) -> T:
    """Build `model_type` from decoded JSON, validating types and enum values."""
    return _decode(model_type, data, model_type.__name__)


def _decode(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    if tp is Any:
        return value
    if origin is Union or origin is types.UnionType:
        options = typing.get_args(tp)
        if value is None and type(None) in options:
            return None
        errors = []
        for option in options:
            if option is type(None):
                continue
            try:
                return _decode(option, value, where)
            except CodecError as err:
                errors.append(str(err))
        raise CodecError(f"{where}: no union member matched ({'; '.join(errors)})")
    if getattr(tp, "__supertype__", None) is not None:  # NewType
        return _decode(tp.__supertype__, value, where)
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, Mapping):
            raise CodecError(f"{where}: expected object, got {type(value).__name__}")
        hints = _hints(tp)
        kwargs = {}
        for f in dataclasses.fields(tp):
            if f.name in value:
                kwargs[f.name] = _decode(hints[f.name], value[f.name], f"{where}.{f.name}")
            elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                raise CodecError(f"{where}: missing field {f.name!r}")
        return tp(**kwargs)
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(value)
        except ValueError as err:
            raise CodecError(f"{where}: {value!r} is not a valid {tp.__name__}") from err
    if origin in (tuple,):
        args = typing.get_args(tp)
        if not isinstance(value, list | tuple):
            raise CodecError(f"{where}: expected array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v, f"{where}[{i}]") for i, v in enumerate(value))
        if len(args) != len(value):
            raise CodecError(f"{where}: expected {len(args)} items")
        return tuple(
            _decode(a, v, f"{where}[{i}]") for i, (a, v) in enumerate(zip(args, value, strict=True))
        )
    if origin in (list,):
        (arg,) = typing.get_args(tp)
        if not isinstance(value, list):
            raise CodecError(f"{where}: expected array")
        return [_decode(arg, v, f"{where}[{i}]") for i, v in enumerate(value)]
    if origin in (dict, Mapping, typing.Mapping) or tp in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise CodecError(f"{where}: expected object")
        args = typing.get_args(tp)
        val_tp = args[1] if len(args) == 2 else Any
        return {str(k): _decode(val_tp, v, f"{where}.{k}") for k, v in value.items()}
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CodecError(f"{where}: expected number, got {value!r}")
        return float(value)
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CodecError(f"{where}: expected integer, got {value!r}")
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise CodecError(f"{where}: expected boolean, got {value!r}")
        return value
    if tp is str:
        if not isinstance(value, str):
            raise CodecError(f"{where}: expected string, got {value!r}")
        return value
    if tp is type(None):
        if value is not None:
            raise CodecError(f"{where}: expected null")
        return None
    if isinstance(tp, typing.TypeAliasType if hasattr(typing, "TypeAliasType") else ()):
        return _decode(tp.__value__, value, where)
    # JsonValue and other recursive aliases: accept JSON data as-is.
    return value
