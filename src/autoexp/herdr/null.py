"""HerdrAdapter used when the master runs without Herdr (`--no-herdr`).

Workers are then started by hand (`autoexp worker serve --worker w1`); there is
no pane to read or type into.
"""

from __future__ import annotations

from autoexp.herdr.adapter import AgentState, PaneRef
from autoexp.herdr.cli_adapter import HerdrError


class NullHerdr:
    def create_workspace(self, label: str, cwd: str) -> PaneRef:
        raise HerdrError("no Herdr: start workers by hand")

    def split_pane(self, pane_id: str, direction: str, cwd: str, env: dict[str, str]) -> PaneRef:
        raise HerdrError("no Herdr: start workers by hand")

    def run(self, pane_id: str, command: str) -> None:
        raise HerdrError("no Herdr")

    def read(self, pane_id: str, lines: int) -> str:
        return ""

    def send_text(self, pane_id: str, text: str) -> None:
        raise HerdrError("no Herdr")

    def send_keys(self, pane_id: str, *keys: str) -> None:
        raise HerdrError("no Herdr")

    def agent_state(self, pane_id: str) -> AgentState:
        return AgentState.UNKNOWN

    def pane_exists(self, pane_id: str) -> bool:
        return False

    def rename(self, pane_id: str, label: str) -> None:
        return None
