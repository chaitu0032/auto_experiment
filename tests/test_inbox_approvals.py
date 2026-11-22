"""Durable worker inbox and pending approvals."""

from __future__ import annotations

import pytest

from autoexp.core.ids import WorkerId
from autoexp.core.models import ApprovalKind, ApprovalStatus, InboxKind
from autoexp.state.approvals import ApprovalError, FileApprovalStore
from autoexp.state.inbox import FileInbox
from autoexp.state.layout import StateLayout

W1 = WorkerId("w1")


def test_post_is_idempotent_and_ordered(layout: StateLayout) -> None:
    inbox = FileInbox(layout)
    first = inbox.post(W1, "r0001-assign", InboxKind.ASSIGN, {"run_id": "r0001"})
    assert first is not None and first.seq == 1
    assert inbox.post(W1, "r0001-assign", InboxKind.ASSIGN, {"run_id": "r0001"}) is None
    inbox.post(W1, "r0002-assign", InboxKind.ASSIGN, {"run_id": "r0002"})
    assert [m.msg_id for m in inbox.pending(W1)] == ["r0001-assign", "r0002-assign"]
    inbox.ack(first)
    assert inbox.is_handled(W1, "r0001-assign")
    assert inbox.exists(W1, "r0001-assign")
    assert inbox.post(W1, "r0001-assign", InboxKind.ASSIGN, {}) is None, "handled ids stay taken"
    assert [m.msg_id for m in inbox.pending(W1)] == ["r0002-assign"]


def test_msg_id_prefix_does_not_collide(layout: StateLayout) -> None:
    inbox = FileInbox(layout)
    inbox.post(W1, "r0001-stop", InboxKind.STOP, {})
    assert not inbox.exists(W1, "1-stop")
    assert inbox.post(W1, "1-stop", InboxKind.STOP, {}) is not None


def test_invalid_msg_id_rejected(layout: StateLayout) -> None:
    with pytest.raises(ValueError):
        FileInbox(layout).post(W1, "../escape", InboxKind.NOTE, {})


def test_approval_lifecycle(layout: StateLayout) -> None:
    store = FileApprovalStore(layout)
    request = store.request(
        ApprovalKind.CODE_CHANGE, "add obs norm", {"diff": "..."}, "decision:llm"
    )
    assert request.approval_id == "a001-code-change"
    assert [a.approval_id for a in store.pending()] == [request.approval_id]
    answered = store.answer(request.approval_id, ApprovalStatus.APPROVED, "ok")
    assert answered.status is ApprovalStatus.APPROVED
    assert store.pending() == ()
    with pytest.raises(ApprovalError):
        store.answer(request.approval_id, ApprovalStatus.REJECTED)


def test_approval_explicit_id_is_idempotent(layout: StateLayout) -> None:
    store = FileApprovalStore(layout)
    a = store.request(
        ApprovalKind.BUDGET_EXTENSION, "more time", {}, "master", approval_id="budget-x"
    )
    b = store.request(ApprovalKind.BUDGET_EXTENSION, "other", {}, "master", approval_id="budget-x")
    assert a == b
    with pytest.raises(ApprovalError):
        store.request(ApprovalKind.BUDGET_EXTENSION, "bad", {}, "master", approval_id="Bad Id")
    with pytest.raises(ApprovalError):
        store.answer("missing", ApprovalStatus.APPROVED)
    with pytest.raises(ApprovalError):
        store.answer("budget-x", ApprovalStatus.PENDING)
    assert store.get("../x") is None
