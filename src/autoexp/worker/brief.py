"""Render the worker agent's brief (the text Claude Code starts with).

Contract it states: you are worker <id>; you execute, the master decides; on a
nudge run `autoexp worker handle`; for DEBUG messages read the run's
stdout.log, diagnose, and report with `autoexp worker report`; never edit
training code without an approved code_change approval; instructions typed
here by the human override master messages - mark that with `autoexp worker
takeover <id> on`.
"""

from __future__ import annotations

from autoexp.core.ids import WorkerId


def render_brief(worker_id: WorkerId, state_dir: str, autoexp_command: str) -> str:
    raise NotImplementedError
