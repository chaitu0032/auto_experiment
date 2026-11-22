"""Dashboard backend skeleton: route matching and static file resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoexp.dashboard import handlers
from autoexp.dashboard.routes import ROUTES, match
from autoexp.dashboard.server import WEB_ROOT, resolve_static


def test_every_route_names_an_existing_handler() -> None:
    for route in ROUTES:
        assert callable(getattr(handlers, route.handler)), route.handler


def test_match_extracts_validated_params() -> None:
    found = match("GET", "/api/runs/r0003/curve")
    assert found is not None
    assert found.route.handler == "get_curve"
    assert found.params == {"run_id": "r0003"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/runs/../curve"),
        ("GET", "/api/runs/r1/curve"),
        ("POST", "/api/overview"),
        ("GET", "/api/stop"),
        ("GET", "/api/workers/w1/pane/extra"),
    ],
)
def test_match_rejects(method: str, path: str) -> None:
    assert match(method, path) is None


def test_mutating_routes_are_posts() -> None:
    assert {r.handler for r in ROUTES if r.mutating} == {
        "answer_approval",
        "set_takeover",
        "request_stop",
    }
    assert all(r.method == "POST" for r in ROUTES if r.mutating)


def test_static_resolves_frontend_files() -> None:
    assert resolve_static("/") == (WEB_ROOT / "index.html").resolve()
    assert resolve_static("/static/js/main.js") == (WEB_ROOT / "js" / "main.js").resolve()


@pytest.mark.parametrize(
    "path", ["/static/../server.py", "/static/js/missing.js", "/etc/passwd", "/static/../../x"]
)
def test_static_refuses_outside_or_missing(path: str) -> None:
    assert resolve_static(path) is None


def test_index_loads_main_module() -> None:
    html = (WEB_ROOT / "index.html").read_text()
    assert '<script type="module" src="/static/js/main.js">' in html
    for module in ("api", "store", "refresh", "dom", "format"):
        assert (WEB_ROOT / "js" / f"{module}.js").is_file()
    assert Path(WEB_ROOT / "js" / "charts" / "lineChart.js").is_file()
