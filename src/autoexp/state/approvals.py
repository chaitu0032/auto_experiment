"""Pending-approval records for actions that need the user first.

The master files a request (training-code change, budget extension) and keeps
working on everything else. The user answers with `autoexp approve|reject` or
the dashboard, which writes `<approval_id>.decision.json`; the master reads the
answer on a later tick and only then performs or drops the gated action.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from autoexp.core.models import ApprovalKind, ApprovalRequest, ApprovalStatus, JsonValue
from autoexp.state.layout import StateLayout


class ApprovalStore(Protocol):
    def request(
        self, kind: ApprovalKind, summary: str, payload: Mapping[str, JsonValue], requested_by: str
    ) -> ApprovalRequest: ...

    def answer(self, approval_id: str, status: ApprovalStatus, note: str = "") -> None: ...

    def get(self, approval_id: str) -> ApprovalRequest | None: ...

    def pending(self) -> tuple[ApprovalRequest, ...]: ...


class FileApprovalStore:
    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout

    def request(
        self, kind: ApprovalKind, summary: str, payload: Mapping[str, JsonValue], requested_by: str
    ) -> ApprovalRequest:
        raise NotImplementedError

    def answer(self, approval_id: str, status: ApprovalStatus, note: str = "") -> None:
        raise NotImplementedError

    def get(self, approval_id: str) -> ApprovalRequest | None:
        raise NotImplementedError

    def pending(self) -> tuple[ApprovalRequest, ...]:
        raise NotImplementedError
