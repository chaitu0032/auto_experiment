"""Pending-approval records for actions that need the user first.

The master files a request (training-code change, budget extension) and keeps
working on everything else. The user answers with `autoexp approve|reject` or
the dashboard, which writes `<approval_id>.decision.json`; the master reads the
answer on a later tick and only then performs or drops the gated action.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from autoexp.core.models import ApprovalKind, ApprovalRequest, ApprovalStatus, JsonValue
from autoexp.state.codec import CodecError, decode, encode
from autoexp.state.jsonl import read_json, write_json_atomic
from autoexp.state.layout import StateLayout

APPROVAL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ApprovalError(ValueError):
    pass


class ApprovalStore(Protocol):
    def request(
        self,
        kind: ApprovalKind,
        summary: str,
        payload: Mapping[str, JsonValue],
        requested_by: str,
        approval_id: str | None = None,
    ) -> ApprovalRequest: ...

    def answer(
        self, approval_id: str, status: ApprovalStatus, note: str = ""
    ) -> ApprovalRequest: ...

    def get(self, approval_id: str) -> ApprovalRequest | None: ...

    def all(self) -> tuple[ApprovalRequest, ...]: ...

    def pending(self) -> tuple[ApprovalRequest, ...]: ...


class FileApprovalStore:
    def __init__(self, layout: StateLayout) -> None:
        self._layout = layout

    def request(
        self,
        kind: ApprovalKind,
        summary: str,
        payload: Mapping[str, JsonValue],
        requested_by: str,
        approval_id: str | None = None,
    ) -> ApprovalRequest:
        """File a request. With an explicit `approval_id` the call is idempotent."""
        if approval_id is None:
            approval_id = f"a{len(self._ids()) + 1:03d}-{kind.value.replace('_', '-')}"
        if APPROVAL_ID_PATTERN.match(approval_id) is None:
            raise ApprovalError(f"invalid approval id: {approval_id!r}")
        existing = self.get(approval_id)
        if existing is not None:
            return existing
        request = ApprovalRequest(
            approval_id=approval_id,
            kind=kind,
            summary=summary,
            payload=dict(payload),
            requested_by=requested_by,
            at=time.time(),
        )
        write_json_atomic(self._layout.approval(approval_id), encode(request))
        return request

    def answer(self, approval_id: str, status: ApprovalStatus, note: str = "") -> ApprovalRequest:
        if status is ApprovalStatus.PENDING:
            raise ApprovalError("an answer must approve or reject")
        current = self.get(approval_id)
        if current is None:
            raise ApprovalError(f"no approval {approval_id}")
        if current.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"approval {approval_id} is already {current.status.value}")
        write_json_atomic(
            self._layout.approval_decision(approval_id),
            {"status": status.value, "note": note, "at": time.time()},
        )
        return replace(current, status=status)

    def get(self, approval_id: str) -> ApprovalRequest | None:
        if APPROVAL_ID_PATTERN.match(approval_id) is None:
            return None
        raw = read_json(self._layout.approval(approval_id))
        if raw is None:
            return None
        try:
            request = decode(ApprovalRequest, raw)
        except CodecError:
            return None
        decision = read_json(self._layout.approval_decision(approval_id))
        if isinstance(decision, dict) and decision.get("status") in ("approved", "rejected"):
            return replace(request, status=ApprovalStatus(decision["status"]))
        return request

    def _ids(self) -> list[str]:
        directory = self._layout.approvals_dir
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.json") if not p.stem.endswith(".decision"))

    def all(self) -> tuple[ApprovalRequest, ...]:
        records = (self.get(i) for i in self._ids())
        return tuple(sorted((r for r in records if r is not None), key=lambda r: r.at))

    def pending(self) -> tuple[ApprovalRequest, ...]:
        return tuple(r for r in self.all() if r.status is ApprovalStatus.PENDING)
