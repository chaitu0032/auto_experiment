"""LLMClient protocol and the Claude Code headless implementation.

`ClaudeCodeClient` runs the configured command (default `claude -p
--output-format json`) with the prompt on stdin, a timeout, and parses the JSON
envelope (result text, cost, usage). Tests use a fake client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LLMReply:
    text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    duration_s: float


class LLMError(RuntimeError):
    pass


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        """Return the reply or raise LLMError; `purpose` labels the metered call."""
        ...


class ClaudeCodeClient:
    def __init__(self, command: tuple[str, ...], timeout_s: float) -> None:
        self._command = command
        self._timeout_s = timeout_s

    def complete(self, prompt: str, *, purpose: str) -> LLMReply:
        raise NotImplementedError
