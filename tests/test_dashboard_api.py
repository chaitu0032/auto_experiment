"""Dashboard JSON API over a real (finished) experiment, served on a free port."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from autoexp.config.schema import DashboardConfig
from autoexp.core.ids import WorkerId
from autoexp.core.models import ApprovalKind
from autoexp.dashboard import handlers
from autoexp.dashboard import server as dashboard
from autoexp.dashboard.routes import ROUTES, match
from autoexp.dashboard.server import WEB_ROOT, resolve_static
from autoexp.state.approvals import FileApprovalStore
from autoexp.state.jsonl import write_json_atomic
from autoexp.state.layout import StateLayout
from tests.test_master_loop import Workers, _master, loop_config


@pytest.fixture(scope="module")
def state(tmp_path_factory: pytest.TempPathFactory) -> StateLayout:
    root = tmp_path_factory.mktemp("dash") / "exp"
    master, layout = _master(loop_config(), root)
    with Workers(layout):
        master.run()
    write_json_atomic(
        layout.worker_pane_tail(WorkerId("w1")),
        {"worker_id": "w1", "pane_id": "p1", "text": "a\nb\nc", "read_at": 1.0},
    )
    FileApprovalStore(layout).request(
        ApprovalKind.CODE_CHANGE,
        "normalise obs",
        {"diff": "--- a"},
        "decision:llm",
        approval_id="code-r9-1",
    )
    return layout


@pytest.fixture(scope="module")
def base_url(state: StateLayout) -> Iterator[str]:
    server = dashboard.start(state, DashboardConfig(host="127.0.0.1", port=0))
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        dashboard.stop(server)


def _get(url: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def _post(
    url: str, body: dict[str, Any], origin: str | None, ctype: str = "application/json"
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": ctype}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_overview(base_url: str) -> None:
    status, body = _get(f"{base_url}/api/overview")
    assert status == 200 and body["ok"] and body["error"] is None
    data = body["data"]
    assert data["goal"]["threshold"] == 475.0 and data["master"] == "stopped"
    assert len(data["runs"]) >= 6 and data["runs"][0]["run_id"] == "r0001"
    assert data["finding"]["stop_rule"] == "goal_reached"
    assert {w["worker_id"] for w in data["workers"]} == {"w1", "w2"}
    assert data["budget"]["lines"][0]["fraction"] >= 0


def test_run_curve_events_pane(base_url: str) -> None:
    status, body = _get(f"{base_url}/api/runs/r0001")
    assert status == 200 and body["data"]["events"][0]["kind"] == "allocated"
    status, body = _get(f"{base_url}/api/runs/r0004/curve?max_points=5")
    curve = body["data"]
    assert status == 200 and 0 < len(curve["returns"]) <= 5 and curve["losses"]
    status, body = _get(f"{base_url}/api/runs/r0004/curve?since_step={curve['next_step']}")
    assert body["data"]["returns"] == []
    status, body = _get(f"{base_url}/api/events?kinds=prune,decision")
    kinds = {e["kind"] for e in body["data"]["events"]}
    assert kinds == {"prune", "decision"}
    seq = body["data"]["next_seq"]
    assert _get(f"{base_url}/api/events?after={seq}")[1]["data"]["events"] == []
    status, body = _get(f"{base_url}/api/workers/w1/pane?lines=2")
    assert body["data"]["text"] == "b\nc"


@pytest.mark.parametrize(
    ("path", "code", "status"),
    [
        ("/api/runs/r0999", "not_found", 404),
        ("/api/runs/r0001/curve?max_points=0", "bad_request", 400),
        ("/api/events?kinds=evil", "bad_request", 400),
        ("/api/events?after=x", "bad_request", 400),
        ("/api/workers/w9/pane", "not_found", 404),
        ("/api/nope", "not_found", 404),
        ("/static/../server.py", "not_found", 404),
    ],
)
def test_errors_use_the_envelope(base_url: str, path: str, code: str, status: int) -> None:
    got, body = _get(base_url + path)
    assert got == status and not body["ok"] and body["error"]["code"] == code


def test_static_frontend_served(base_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/", timeout=5) as resp:
        html = resp.read().decode()
        assert resp.headers["X-Frame-Options"] == "DENY"
    assert '<script type="module" src="/static/js/main.js">' in html
    with urllib.request.urlopen(f"{base_url}/static/js/api.js", timeout=5) as resp:
        assert resp.headers["Content-Type"].startswith("text/javascript")


def test_posts_require_same_origin_and_json(base_url: str, state: StateLayout) -> None:
    url = f"{base_url}/api/approvals/code-r9-1"
    assert _post(url, {"decision": "approve"}, None)[0] == 403
    assert _post(url, {"decision": "approve"}, "http://evil.example")[0] == 403
    assert _post(url, {"decision": "approve"}, base_url, "text/plain")[0] == 415
    assert _post(url, {"decision": "maybe"}, base_url)[0] == 400
    status, body = _post(url, {"decision": "approve", "note": "ok"}, base_url)
    assert status == 200 and body["data"]["status"] == "approved"
    assert _post(url, {"decision": "reject", "note": ""}, base_url)[0] == 409
    assert _post(f"{base_url}/api/approvals/none", {"decision": "approve"}, base_url)[0] == 404


def test_takeover_and_stop(base_url: str, state: StateLayout) -> None:
    status, body = _post(f"{base_url}/api/workers/w2/takeover", {"on": True}, base_url)
    assert status == 200 and body["data"]["takeover"] is True
    assert state.worker_takeover(WorkerId("w2")).exists()
    assert _post(f"{base_url}/api/workers/w2/takeover", {"on": "yes"}, base_url)[0] == 400
    _post(f"{base_url}/api/workers/w2/takeover", {"on": False}, base_url)
    assert not state.worker_takeover(WorkerId("w2")).exists()
    status, body = _post(f"{base_url}/api/stop", {}, base_url)
    assert status == 200 and body["data"]["stop_requested"] is True


def test_stream_yields_version_events(state: StateLayout) -> None:
    chunks = handlers.stream(state, iter([True, True, False]).__next__)
    assert next(chunks).startswith("retry:")
    first = next(chunks)
    assert first.startswith("event: version") and '"version"' in first


def test_routes_and_static_resolution() -> None:
    for route in ROUTES:
        assert callable(getattr(handlers, route.handler)), route.handler
    found = match("GET", "/api/runs/r0003/curve")
    assert found is not None and found.params == {"run_id": "r0003"}
    for method, path in [
        ("GET", "/api/runs/../curve"),
        ("GET", "/api/runs/r1/curve"),
        ("POST", "/api/overview"),
        ("GET", "/api/stop"),
    ]:
        assert match(method, path) is None
    assert {r.handler for r in ROUTES if r.mutating} == {
        "answer_approval",
        "set_takeover",
        "request_stop",
    }
    assert resolve_static("/") == (WEB_ROOT / "index.html").resolve()
    for bad in ("/static/../server.py", "/static/js/missing.js", "/etc/passwd"):
        assert resolve_static(bad) is None
    assert (WEB_ROOT / "js" / "charts" / "lineChart.js").is_file()
    assert Path(WEB_ROOT / "index.html").read_text().count("<section") == 7
