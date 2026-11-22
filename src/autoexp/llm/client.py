"""LLMClient protocol, the Claude Code headless client, and the metering wrapper.

`ClaudeCodeClient` runs the configured command (default `claude -p
--output-format json`) with the prompt on stdin and a timeout, and parses the
JSON envelope (result text, cost, usage). `MeteredLLMClient` wraps any client:
it refuses calls once the LLM budget is spent and reports every call to a
sink (the master event log), which is what the budget ledger counts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LLMReply:
    text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    duration_s: float


class LLMError(RuntimeError):
    pass


class LLMBudgetExhausted(LLMError):
    pass


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        """Return the reply or raise LLMError; `purpose` labels the metered call."""
        ...


# Variables that tie a process to an enclosing Claude Code session. A headless
# call made by the master must be its own session, never a child of whatever
# session happened to start the master.
SESSION_ENV_VARS = (
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_CHILD_NOTES_DIR",
    "CLAUDE_CODE_ENTRYPOINT",
)


def detached_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in SESSION_ENV_VARS}


class ClaudeCodeClient:
    def __init__(self, command: tuple[str, ...], timeout_s: float) -> None:
        self._command = command
        self._timeout_s = timeout_s

    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                list(self._command),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                env=detached_env(),
            )
        except FileNotFoundError as err:
            raise LLMError(f"LLM command not found: {self._command[0]}") from err
        except subprocess.TimeoutExpired as err:
            raise LLMError(f"LLM call timed out after {self._timeout_s:.0f}s") from err
        duration = time.monotonic() - started
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-300:]
            raise LLMError(f"LLM command exited {proc.returncode}: {tail}")
        return parse_envelope(proc.stdout, duration)


def parse_envelope(stdout: str, duration_s: float) -> LLMReply:
    """Parse `claude -p --output-format json` output; plain text is accepted too."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return LLMReply(stdout.strip(), 0.0, 0, 0, duration_s)
    if not isinstance(envelope, dict):
        raise LLMError("unexpected LLM output shape")
    if envelope.get("is_error"):
        raise LLMError(f"LLM reported an error: {str(envelope.get('result', ''))[:300]}")
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    return LLMReply(
        text=str(envelope.get("result", "")),
        cost_usd=float(envelope.get("total_cost_usd") or 0.0),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        duration_s=duration_s,
    )


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """The first JSON object in a reply (fenced or bare); raises LLMError."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise LLMError("no JSON object in LLM reply")


@dataclass(frozen=True, slots=True)
class CallRecord:
    purpose: str
    ok: bool
    cost_usd: float
    input_tokens: int
    output_tokens: int
    duration_s: float
    error: str | None


class MeteredLLMClient:
    """Budget gate + metering around another client."""

    def __init__(
        self,
        inner: LLMClient,
        budget_ok: Callable[[], bool],
        sink: Callable[[CallRecord], None],
    ) -> None:
        self._inner = inner
        self._budget_ok = budget_ok
        self._sink = sink

    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        if not self._budget_ok():
            raise LLMBudgetExhausted("LLM budget exhausted")
        try:
            reply = self._inner.complete(prompt, purpose=purpose)
        except LLMError as err:
            self._sink(CallRecord(purpose, False, 0.0, 0, 0, 0.0, str(err)[:300]))
            raise
        self._sink(
            CallRecord(
                purpose,
                True,
                reply.cost_usd,
                reply.input_tokens,
                reply.output_tokens,
                reply.duration_s,
                None,
            )
        )
        return reply
