"""`autoexp worker handle`: process a worker's inbox once, mechanically.

ASSIGN -> spawn a detached run supervisor (`autoexp run-exec <run_id>`) and
append an `assigned`/`launched` trail; STOP -> write runs/<id>/control.json;
DEBUG and APPROVAL_RESULT -> print them for the agent and leave the decision to
it (the shell runtime reports them as blocked). Each handled message is acked.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoexp.core.ids import WorkerId
from autoexp.core.models import InboxMessage
from autoexp.state.inbox import Inbox
from autoexp.state.layout import StateLayout


@dataclass(frozen=True, slots=True)
class HandleResult:
    handled: tuple[InboxMessage, ...]
    needs_agent: tuple[InboxMessage, ...]  # DEBUG / APPROVAL_RESULT
    errors: tuple[str, ...]


def handle_inbox(layout: StateLayout, inbox: Inbox, worker_id: WorkerId) -> HandleResult:
    raise NotImplementedError
