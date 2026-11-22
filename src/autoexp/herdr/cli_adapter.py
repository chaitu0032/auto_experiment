"""HerdrAdapter over the `herdr` CLI.

Every call is `[*command_prefix, <subcommand...>]` plus `--session <name>` when
a session is configured, run with a timeout and checked exit status. The prefix
is configurable so a deployment can target a named session or a wrapper script.
"""

from __future__ import annotations

from autoexp.herdr.adapter import AgentState, PaneRef


class HerdrError(RuntimeError):
    pass


class CliHerdrAdapter:
    def __init__(
        self, command_prefix: tuple[str, ...], session: str | None, timeout_s: float = 15.0
    ) -> None:
        self._prefix = command_prefix
        self._session = session
        self._timeout_s = timeout_s

    def create_workspace(self, label: str, cwd: str) -> PaneRef:
        raise NotImplementedError

    def split_pane(self, pane_id: str, direction: str, cwd: str, env: dict[str, str]) -> PaneRef:
        raise NotImplementedError

    def run(self, pane_id: str, command: str) -> None:
        raise NotImplementedError

    def read(self, pane_id: str, lines: int) -> str:
        raise NotImplementedError

    def send_text(self, pane_id: str, text: str) -> None:
        raise NotImplementedError

    def send_keys(self, pane_id: str, *keys: str) -> None:
        raise NotImplementedError

    def agent_state(self, pane_id: str) -> AgentState:
        raise NotImplementedError

    def pane_exists(self, pane_id: str) -> bool:
        raise NotImplementedError
