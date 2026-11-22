"""Route table of the dashboard JSON API.

The table and the matcher are real code; handlers are named here and
implemented in `handlers.py`. Paths are matched exactly per segment, path
parameters are validated by pattern before any handler sees them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Method = Literal["GET", "POST"]

_PARAM_PATTERNS = {
    "run_id": re.compile(r"^r\d{4,}$"),
    "worker_id": re.compile(r"^w\d+$"),
    "approval_id": re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$"),
}


@dataclass(frozen=True, slots=True)
class Route:
    method: Method
    pattern: str  # e.g. "/api/runs/{run_id}/curve"
    handler: str  # name of the function in handlers.py
    response: str  # name of the DTO in schemas.py (or "sse")
    mutating: bool = False


ROUTES: tuple[Route, ...] = (
    Route("GET", "/api/overview", "get_overview", "OverviewDTO"),
    Route("GET", "/api/runs/{run_id}", "get_run", "RunDetailDTO"),
    Route("GET", "/api/runs/{run_id}/curve", "get_curve", "CurveDTO"),
    Route("GET", "/api/events", "get_events", "EventsDTO"),
    Route("GET", "/api/workers/{worker_id}/pane", "get_pane", "PaneDTO"),
    Route("GET", "/api/stream", "stream", "sse"),
    Route("POST", "/api/approvals/{approval_id}", "answer_approval", "ApprovalDTO", True),
    Route("POST", "/api/workers/{worker_id}/takeover", "set_takeover", "WorkerDTO", True),
    Route("POST", "/api/stop", "request_stop", "OverviewDTO", True),
)


@dataclass(frozen=True, slots=True)
class Match:
    route: Route
    params: dict[str, str]


def match(method: str, path: str) -> Match | None:
    """Return the route and validated path params for a request, or None."""
    segments = path.rstrip("/").split("/") if path != "/" else [""]
    for route in ROUTES:
        if route.method != method:
            continue
        params = _match_segments(route.pattern.split("/"), segments)
        if params is not None:
            return Match(route=route, params=params)
    return None


def _match_segments(pattern: list[str], segments: list[str]) -> dict[str, str] | None:
    if len(pattern) != len(segments):
        return None
    params: dict[str, str] = {}
    for want, got in zip(pattern, segments, strict=True):
        if want.startswith("{") and want.endswith("}"):
            name = want[1:-1]
            if _PARAM_PATTERNS[name].match(got) is None:
                return None
            params[name] = got
        elif want != got:
            return None
    return params
