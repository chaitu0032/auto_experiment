"""HerdrAdapter over the `herdr` CLI.

Every call is `[*command_prefix, <subcommand...>]` plus a trailing
`--session <name>` when a session is configured, run with a timeout and
checked exit status. Herdr answers JSON envelopes: `{"result": {...}}` or
`{"error": {"code", "message"}}`; `pane read` answers plain text.
The prefix is configurable (config `herdr.command`, or the
`AUTOEXP_HERDR_COMMAND` environment variable, shell-split) so a deployment can
target a named session or a wrapper script.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

from autoexp.herdr.adapter import AgentState, PaneRef

ENV_OVERRIDE = "AUTOEXP_HERDR_COMMAND"
# Variables a pane inherits from an outer Herdr session; they must not leak into
# calls aimed at another session.
_SCRUB = (
    "HERDR_SOCKET_PATH",
    "HERDR_PANE_ID",
    "HERDR_TAB_ID",
    "HERDR_WORKSPACE_ID",
    "HERDR_SESSION",
)


class HerdrError(RuntimeError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def resolve_prefix(configured: tuple[str, ...]) -> tuple[str, ...]:
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    return tuple(shlex.split(override)) if override else configured


class CliHerdrAdapter:
    def __init__(
        self, command_prefix: tuple[str, ...], session: str | None, timeout_s: float = 15.0
    ) -> None:
        self._prefix = resolve_prefix(command_prefix)
        # A wrapper from the environment supplies its own session.
        self._session = None if os.environ.get(ENV_OVERRIDE) else session
        self._timeout_s = timeout_s

    def _argv(self, args: list[str]) -> list[str]:
        argv = [*self._prefix, *args]
        if self._session:
            argv += ["--session", self._session]
        return argv

    def _call(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
        try:
            return subprocess.run(
                self._argv(args),
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                env=env,
            )
        except FileNotFoundError as err:
            raise HerdrError(f"herdr command not found: {self._prefix[0]}") from err
        except subprocess.TimeoutExpired as err:
            raise HerdrError(f"herdr {' '.join(args[:2])} timed out") from err

    def _json(self, args: list[str]) -> dict[str, Any]:
        proc = self._call(args)
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            err = payload["error"]
            raise HerdrError(str(err.get("message", "herdr error")), str(err.get("code", "")))
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-300:]
            raise HerdrError(f"herdr {' '.join(args[:2])} failed ({proc.returncode}): {tail}")
        result = payload.get("result") if isinstance(payload, dict) else None
        return result if isinstance(result, dict) else {}

    def _plain(self, args: list[str]) -> str:
        proc = self._call(args)
        if proc.returncode != 0:
            message = proc.stdout.strip() or proc.stderr.strip()
            code = None
            try:
                parsed = json.loads(message)
                code = parsed.get("error", {}).get("code")
                message = parsed.get("error", {}).get("message", message)
            except (json.JSONDecodeError, AttributeError):
                pass
            raise HerdrError(f"herdr {' '.join(args[:2])} failed: {message[-300:]}", code)
        return proc.stdout

    @staticmethod
    def _pane_ref(pane: dict[str, Any]) -> PaneRef:
        return PaneRef(pane_id=str(pane["pane_id"]), workspace_id=str(pane.get("workspace_id", "")))

    def create_workspace(self, label: str, cwd: str) -> PaneRef:
        result = self._json(["workspace", "create", "--cwd", cwd, "--label", label, "--no-focus"])
        return self._pane_ref(result["root_pane"])

    def split_pane(self, pane_id: str, direction: str, cwd: str, env: dict[str, str]) -> PaneRef:
        args = ["pane", "split", pane_id, "--direction", direction, "--cwd", cwd, "--no-focus"]
        for key, value in env.items():
            args += ["--env", f"{key}={value}"]
        return self._pane_ref(self._json(args)["pane"])

    def rename(self, pane_id: str, label: str) -> None:
        self._json(["pane", "rename", pane_id, label])

    def run(self, pane_id: str, command: str) -> None:
        self._plain(["pane", "run", pane_id, command])

    def read(self, pane_id: str, lines: int) -> str:
        return self._plain(
            ["pane", "read", pane_id, "--source", "recent-unwrapped", "--lines", str(lines)]
        )

    def send_text(self, pane_id: str, text: str) -> None:
        self._plain(["pane", "send-text", pane_id, text])

    def send_keys(self, pane_id: str, *keys: str) -> None:
        self._plain(["pane", "send-keys", pane_id, *keys])

    def agent_state(self, pane_id: str) -> AgentState:
        pane = self._json(["pane", "get", pane_id]).get("pane", {})
        try:
            return AgentState(str(pane.get("agent_status", "unknown")))
        except ValueError:
            return AgentState.UNKNOWN

    def pane_exists(self, pane_id: str) -> bool:
        try:
            self._json(["pane", "get", pane_id])
        except HerdrError as err:
            if err.code == "pane_not_found":
                return False
            raise
        return True

    def foreground_commands(self, pane_id: str) -> tuple[str, ...]:
        info = self._json(["pane", "process-info", "--pane", pane_id]).get("process_info", {})
        return tuple(str(p.get("cmdline", "")) for p in info.get("foreground_processes", []))
