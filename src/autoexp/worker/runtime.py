"""Worker runtimes: what runs inside a worker's Herdr pane.

- ClaudeCodeRuntime (seam 3): Claude Code started with the worker brief and a
  narrow tool allowlist; it handles nudges, debugs crashes it is asked about,
  and never changes training code without an approved approval.
- ShellRuntime: `autoexp worker serve`, the same handler in a loop, no agent.
"""

from __future__ import annotations

from typing import Protocol

from autoexp.core.ids import WorkerId


class WorkerRuntime(Protocol):
    name: str

    def start_command(self, worker_id: WorkerId, state_dir: str, brief_path: str) -> str:
        """Shell command line the master runs in the worker's pane."""
        ...


class ClaudeCodeRuntime:
    name = "claude"

    def __init__(self, agent_command: tuple[str, ...], allowed_tools: tuple[str, ...]) -> None:
        self._agent_command = agent_command
        self._allowed_tools = allowed_tools

    def start_command(self, worker_id: WorkerId, state_dir: str, brief_path: str) -> str:
        raise NotImplementedError


class ShellRuntime:
    name = "shell"

    def start_command(self, worker_id: WorkerId, state_dir: str, brief_path: str) -> str:
        raise NotImplementedError
