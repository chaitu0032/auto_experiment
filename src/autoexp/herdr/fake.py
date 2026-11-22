"""In-memory HerdrAdapter for tests: records sent text and keys, scripted reads."""

from __future__ import annotations

from dataclasses import dataclass, field

from autoexp.herdr.adapter import AgentState, PaneRef


@dataclass
class _FakePane:
    ref: PaneRef
    output: str = ""
    state: AgentState = AgentState.IDLE
    sent: list[str] = field(default_factory=list)


class FakeHerdr:
    """Test double. Holds mutable fake pane state by design; never used in production."""

    def __init__(self) -> None:
        self._panes: dict[str, _FakePane] = {}
        self._next = 1

    def _new_pane(self, workspace_id: str) -> PaneRef:
        ref = PaneRef(pane_id=f"p{self._next}", workspace_id=workspace_id)
        self._next += 1
        self._panes[ref.pane_id] = _FakePane(ref=ref)
        return ref

    def create_workspace(self, label: str, cwd: str) -> PaneRef:
        return self._new_pane(workspace_id=f"ws-{label}")

    def split_pane(self, pane_id: str, direction: str, cwd: str, env: dict[str, str]) -> PaneRef:
        return self._new_pane(workspace_id=self._panes[pane_id].ref.workspace_id)

    def run(self, pane_id: str, command: str) -> None:
        self._panes[pane_id].sent.append(f"run:{command}")

    def read(self, pane_id: str, lines: int) -> str:
        return "\n".join(self._panes[pane_id].output.splitlines()[-lines:])

    def send_text(self, pane_id: str, text: str) -> None:
        self._panes[pane_id].sent.append(f"text:{text}")

    def send_keys(self, pane_id: str, *keys: str) -> None:
        self._panes[pane_id].sent.append("keys:" + " ".join(keys))

    def agent_state(self, pane_id: str) -> AgentState:
        return self._panes[pane_id].state

    def pane_exists(self, pane_id: str) -> bool:
        return pane_id in self._panes

    def rename(self, pane_id: str, label: str) -> None:
        self._panes[pane_id].sent.append(f"rename:{label}")

    def close(self, pane_id: str) -> None:
        self._panes.pop(pane_id, None)

    # test helpers
    def set_output(self, pane_id: str, text: str) -> None:
        self._panes[pane_id].output = text

    def set_state(self, pane_id: str, state: AgentState) -> None:
        self._panes[pane_id].state = state

    def sent(self, pane_id: str) -> tuple[str, ...]:
        return tuple(self._panes[pane_id].sent)
