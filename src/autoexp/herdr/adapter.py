"""HerdrAdapter protocol: what the master needs from a terminal multiplexer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class AgentState(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PaneRef:
    pane_id: str
    workspace_id: str


@runtime_checkable
class HerdrAdapter(Protocol):
    def create_workspace(self, label: str, cwd: str) -> PaneRef:
        """Create a workspace and return its first pane."""
        ...

    def split_pane(
        self, pane_id: str, direction: str, cwd: str, env: dict[str, str]
    ) -> PaneRef: ...

    def run(self, pane_id: str, command: str) -> None:
        """Run a shell command line in the pane."""
        ...

    def read(self, pane_id: str, lines: int) -> str:
        """Recent terminal output of the pane (unwrapped text)."""
        ...

    def send_text(self, pane_id: str, text: str) -> None: ...

    def send_keys(self, pane_id: str, *keys: str) -> None: ...

    def agent_state(self, pane_id: str) -> AgentState: ...

    def pane_exists(self, pane_id: str) -> bool: ...
