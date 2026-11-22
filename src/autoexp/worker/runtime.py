"""Worker runtimes: what runs inside a worker's Herdr pane.

- ClaudeCodeRuntime (seam 3): Claude Code started with the worker brief and a
  narrow tool allowlist; it handles nudges, debugs crashes it is asked about,
  and never changes training code without an approved approval.
- ShellRuntime: `autoexp worker serve`, the same handler in a loop, no agent.
"""

from __future__ import annotations

import shlex
from typing import Protocol

from autoexp.core.ids import WorkerId


class WorkerRuntime(Protocol):
    name: str
    uses_agent: bool

    def start_command(self, worker_id: WorkerId, state_dir: str, brief_path: str) -> str:
        """Shell command line the master runs in the worker's pane."""
        ...


class ClaudeCodeRuntime:
    name = "claude"
    uses_agent = True

    def __init__(self, agent_command: tuple[str, ...], autoexp_command: str) -> None:
        self._agent_command = agent_command
        self._autoexp = autoexp_command

    def allowed_tools(self, state_dir: str) -> tuple[str, ...]:
        return (
            f"Bash({self._autoexp} worker:*)",
            "Read",
            "Grep",
            "Glob",
        )

    def start_command(self, worker_id: WorkerId, state_dir: str, brief_path: str) -> str:
        prompt = (
            f"You are autoexp worker {worker_id}. Read your brief at {brief_path} and follow it. "
            f"Start by running: {self._autoexp} worker handle --worker {worker_id}"
        )
        argv = [
            *self._agent_command,
            "--add-dir",
            state_dir,
            "--allowedTools",
            *self.allowed_tools(state_dir),
            "--",
            prompt,
        ]
        return shlex.join(argv)


class ShellRuntime:
    name = "shell"
    uses_agent = False

    def __init__(self, autoexp_command: str) -> None:
        self._autoexp = autoexp_command

    def start_command(self, worker_id: WorkerId, state_dir: str, brief_path: str) -> str:
        return f"{self._autoexp} worker serve --worker {shlex.quote(worker_id)}"
