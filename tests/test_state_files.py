"""JSONL logs, atomic JSON, codec round-trips, ids and layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoexp.core.ids import format_run_id, parse_run_id, validate_device_id, validate_worker_id
from autoexp.core.models import (
    Decision,
    DecisionAction,
    EventSource,
    InboxKind,
    InboxMessage,
    ProposedRun,
    RunEvent,
    RunEventKind,
    RunPurpose,
)
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import append_line, read_json, read_lines, write_json_atomic
from autoexp.state.layout import StateLayout
from tests.conftest import spec


def test_append_and_read_lines(tmp_path: Path) -> None:
    path = tmp_path / "a" / "log.jsonl"
    append_line(path, {"x": 1})
    append_line(path, {"x": 2})
    result = read_lines(path)
    assert [r["x"] for r in result.records] == [1, 2]
    assert result.next_offset == path.stat().st_size
    assert read_lines(path, result.next_offset).records == ()


def test_read_lines_tolerates_partial_and_malformed(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"x": 1}\nnot json\n[1,2]\n{"x": 2}\n{"x": 3')
    result = read_lines(path)
    assert [r["x"] for r in result.records] == [1, 2]
    assert result.malformed == 2
    with path.open("ab") as handle:
        handle.write(b"}\n")
    rest = read_lines(path, result.next_offset)
    assert [r["x"] for r in rest.records] == [3]


def test_read_lines_missing_file(tmp_path: Path) -> None:
    result = read_lines(tmp_path / "nope.jsonl", 7)
    assert result.records == () and result.next_offset == 7


def test_atomic_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "d" / "doc.json"
    write_json_atomic(path, {"a": [1, 2]})
    assert read_json(path) == {"a": [1, 2]}
    assert read_json(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{")
    assert read_json(tmp_path / "bad.json") is None
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".")]


def test_codec_round_trips_models() -> None:
    s = spec(3, purpose=RunPurpose.SEED_CHECK, config={"lr": 0.001, "n": 4, "flag": True})
    assert decode(type(s), encode(s)) == s
    event = RunEvent(s.run_id, 1.5, RunEventKind.EXITED, EventSource.SUPERVISOR, {"exit_code": 0})
    assert decode(RunEvent, encode(event)) == event
    msg = InboxMessage("r0003-assign", 1, "w1", 2.0, InboxKind.ASSIGN, {"run_id": "r0003"})  # type: ignore[arg-type]
    assert decode(InboxMessage, encode(msg)) == msg
    decision = Decision(
        2, DecisionAction.LAUNCH, (ProposedRun({"lr": 0.1}, 1, RunPurpose.FORK),), "why", "llm"
    )
    assert decode(Decision, encode(decision)) == decision


def test_codec_rejects_bad_data() -> None:
    good = encode(spec(1))
    with pytest.raises(CodecError):
        decode(type(spec(1)), {**good, "purpose": "nonsense"})
    with pytest.raises(CodecError):
        decode(type(spec(1)), {k: v for k, v in good.items() if k != "seed"})
    with pytest.raises(CodecError):
        decode(type(spec(1)), {**good, "seed": "1"})
    with pytest.raises(CodecError):
        decode(type(spec(1)), {**good, "seed": True})


def test_run_ids() -> None:
    assert format_run_id(3) == "r0003"
    assert parse_run_id(format_run_id(12345)) == 12345
    for bad in ("", "r1", "x0001", "r00a1"):
        with pytest.raises(ValueError):
            parse_run_id(bad)
    with pytest.raises(ValueError):
        format_run_id(0)
    assert validate_worker_id("w1") == "w1"
    assert validate_device_id("mps0") == "mps0"
    with pytest.raises(ValueError):
        validate_worker_id("worker-1")
    with pytest.raises(ValueError):
        validate_device_id("gpu0")


def test_layout_is_keyed_by_run_id(tmp_path: Path) -> None:
    layout = StateLayout(tmp_path)
    run = format_run_id(7)
    assert layout.run_metrics(run) == tmp_path / "runs" / "r0007" / "metrics.jsonl"
    assert layout.run_lock(run) == tmp_path / "locks" / "run-r0007.lock"
    assert StateLayout.lock_info(layout.master_lock) == tmp_path / "locks" / "master.json"
