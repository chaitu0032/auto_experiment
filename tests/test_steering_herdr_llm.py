"""Steering (human wins), the Herdr CLI adapter, and the LLM client."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from autoexp.core.ids import WorkerId
from autoexp.core.models import InboxKind, WorkerRecord, WorkerState
from autoexp.herdr.adapter import AgentState, HerdrAdapter
from autoexp.herdr.cli_adapter import CliHerdrAdapter, HerdrError
from autoexp.herdr.fake import FakeHerdr
from autoexp.herdr.null import NullHerdr
from autoexp.llm.client import (
    CallRecord,
    ClaudeCodeClient,
    LLMBudgetExhausted,
    LLMError,
    LLMReply,
    MeteredLLMClient,
    extract_json,
    parse_envelope,
)
from autoexp.master.steering import Steering, nudge_text
from autoexp.state.inbox import FileInbox
from autoexp.state.layout import StateLayout


def _worker(pane: str, takeover: bool = False) -> WorkerRecord:
    return WorkerRecord(WorkerId("w1"), pane, "claude", WorkerState.IDLE, takeover, ())


def test_steering_nudges_idle_agent_and_holds_for_humans(layout: StateLayout) -> None:
    herdr = FakeHerdr()
    pane = herdr.create_workspace("x", "/tmp").pane_id
    steering = Steering(FileInbox(layout), herdr, agent_runtime=True)
    result = steering.send(_worker(pane), "r0001-assign", InboxKind.ASSIGN, {"run_id": "r0001"})
    assert result.message is not None and result.nudged
    assert herdr.sent(pane) == (f"text:{nudge_text(WorkerId('w1'), 1)}", "keys:Enter")
    held = steering.send(_worker(pane, takeover=True), "r0002-assign", InboxKind.ASSIGN, {})
    assert not held.nudged and held.held_reason == "human takeover"
    herdr.set_state(pane, AgentState.WORKING)
    busy = steering.nudge_pending(_worker(pane))
    assert not busy.nudged and "busy" in (busy.held_reason or "")
    herdr.set_output(pane, "line1\nline2")
    assert steering.read_context(_worker(pane), 1) == "line2"


def test_shell_workers_are_never_typed_into(layout: StateLayout) -> None:
    herdr = FakeHerdr()
    pane = herdr.create_workspace("x", "/tmp").pane_id
    steering = Steering(FileInbox(layout), herdr, agent_runtime=False)
    result = steering.send(_worker(pane), "r0001-assign", InboxKind.ASSIGN, {})
    assert result.message is not None and not result.nudged and herdr.sent(pane) == ()


def test_adapters_satisfy_protocol() -> None:
    assert isinstance(FakeHerdr(), HerdrAdapter)
    assert isinstance(NullHerdr(), HerdrAdapter)
    assert isinstance(CliHerdrAdapter(("herdr",), None), HerdrAdapter)


FAKE_HERDR = r"""
import json, sys
args = sys.argv[1:]
open(sys.argv[0] + ".log", "a").write(json.dumps(args) + "\n")
if args[:2] == ["workspace", "create"]:
    print(json.dumps({"result": {"root_pane": {"pane_id": "w1:p1", "workspace_id": "w1"}}}))
elif args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane": {"pane_id": "w1:p2", "workspace_id": "w1"}}}))
elif args[:2] == ["pane", "get"] and args[2] == "w9:p9":
    print(json.dumps({"error": {"code": "pane_not_found", "message": "pane w9:p9 not found"}}))
    sys.exit(1)
elif args[:2] == ["pane", "get"]:
    print(json.dumps({"result": {"pane": {"pane_id": args[2], "agent_status": "working"}}}))
elif args[:2] == ["pane", "read"]:
    print("hello from the pane")
elif args[:2] == ["pane", "process-info"]:
    procs = [{"cmdline": "claude"}]
    print(json.dumps({"result": {"process_info": {"foreground_processes": procs}}}))
elif args[:2] == ["pane", "send-text"] and args[3] == "fail":
    print(json.dumps({"error": {"code": "boom", "message": "no"}}))
    sys.exit(1)
else:
    print(json.dumps({"result": {}}))
"""


def _fake_herdr(tmp_path: Path) -> tuple[CliHerdrAdapter, Path]:
    script = tmp_path / "herdr.py"
    script.write_text(FAKE_HERDR)
    return CliHerdrAdapter((sys.executable, str(script)), "lab"), Path(str(script) + ".log")


def test_cli_adapter_parses_herdr_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOEXP_HERDR_COMMAND", raising=False)
    adapter, log = _fake_herdr(tmp_path)
    assert adapter.create_workspace("autoexp", "/tmp").pane_id == "w1:p1"
    assert adapter.split_pane("w1:p1", "right", "/tmp", {"A": "1"}).pane_id == "w1:p2"
    assert adapter.agent_state("w1:p2") is AgentState.WORKING
    assert adapter.read("w1:p2", 10).strip() == "hello from the pane"
    assert adapter.pane_exists("w1:p2") and not adapter.pane_exists("w9:p9")
    assert adapter.foreground_commands("w1:p2") == ("claude",)
    adapter.send_text("w1:p2", "hi")
    adapter.send_keys("w1:p2", "Enter")
    with pytest.raises(HerdrError):
        adapter.send_text("w1:p2", "fail")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(call[-2:] == ["--session", "lab"] for call in calls)
    assert ["--env", "A=1"] == calls[1][calls[1].index("--env") : calls[1].index("--env") + 2]


def test_cli_adapter_env_override_drops_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "herdr.py"
    script.write_text(FAKE_HERDR)
    monkeypatch.setenv("AUTOEXP_HERDR_COMMAND", f"{sys.executable} {script}")
    adapter = CliHerdrAdapter(("herdr",), "ignored")
    adapter.send_keys("w1:p1", "Enter")
    call = json.loads(Path(str(script) + ".log").read_text().splitlines()[-1])
    assert "--session" not in call
    missing = CliHerdrAdapter(("definitely-not-a-binary-xyz",), None)
    monkeypatch.delenv("AUTOEXP_HERDR_COMMAND")
    with pytest.raises(HerdrError, match="not found"):
        CliHerdrAdapter(("definitely-not-a-binary-xyz",), None).send_keys("p", "x")
    assert missing is not None


def test_parse_envelope_and_extract_json() -> None:
    reply = parse_envelope(
        json.dumps(
            {
                "result": "hi",
                "total_cost_usd": 0.5,
                "usage": {"input_tokens": 3, "output_tokens": 4},
            }
        ),
        1.0,
    )
    assert (reply.text, reply.cost_usd, reply.input_tokens, reply.output_tokens) == (
        "hi",
        0.5,
        3,
        4,
    )
    assert parse_envelope("plain text", 0.1).text == "plain text"
    with pytest.raises(LLMError):
        parse_envelope(json.dumps({"is_error": True, "result": "nope"}), 0.1)
    assert extract_json('blah ```json\n{"a": 1}\n``` tail') == {"a": 1}
    assert extract_json('x {"b": [1]} y') == {"b": [1]}
    with pytest.raises(LLMError):
        extract_json("nothing here")


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "fake_claude.py"
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_claude_code_client_runs_command(tmp_path: Path) -> None:
    ok = _script(
        tmp_path, "import sys, json; p = sys.stdin.read(); print(json.dumps({'result': p.upper()}))"
    )
    assert ClaudeCodeClient((sys.executable, ok), 10).complete("hi", purpose="t").text == "HI"
    bad = _script(tmp_path, "import sys; print('err', file=sys.stderr); sys.exit(3)")
    with pytest.raises(LLMError, match="exited 3"):
        ClaudeCodeClient((sys.executable, bad), 10).complete("hi", purpose="t")
    slow = _script(tmp_path, "import time; time.sleep(5)")
    with pytest.raises(LLMError, match="timed out"):
        ClaudeCodeClient((sys.executable, slow), 0.5).complete("hi", purpose="t")
    with pytest.raises(LLMError, match="not found"):
        ClaudeCodeClient(("no-such-llm-binary",), 1).complete("hi", purpose="t")


class _Inner:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        if self.fail:
            raise LLMError("down")
        return LLMReply("ok", 0.1, 1, 1, 0.2)


def test_metered_client_gates_and_records() -> None:
    calls: list[CallRecord] = []
    allowed = [True]
    client = MeteredLLMClient(_Inner(), lambda: allowed[0], calls.append)
    assert client.complete("x", purpose="decision").text == "ok"
    allowed[0] = False
    with pytest.raises(LLMBudgetExhausted):
        client.complete("x", purpose="decision")
    failing = MeteredLLMClient(_Inner(fail=True), lambda: True, calls.append)
    with pytest.raises(LLMError):
        failing.complete("x", purpose="judge")
    assert [(c.purpose, c.ok) for c in calls] == [("decision", True), ("judge", False)]
