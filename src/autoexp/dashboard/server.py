"""Dashboard HTTP server: JSON API + static frontend, bound to loopback.

- `/` and `/static/...` serve files from `dashboard/web/` (index.html, js/,
  css/). `resolve_static()` refuses anything outside that directory.
- `/api/...` is dispatched through `routes.match()` to `handlers.py` and
  wrapped in the envelope from `schemas.py`.
- POSTs must carry `Content-Type: application/json` and an `Origin` equal to
  the server's own origin (403 `forbidden_origin` otherwise); bodies are capped
  at 4 KiB and validated before a handler runs.
- `/api/stream` holds the connection open for server-sent events.

Built on `http.server.ThreadingHTTPServer`; no third-party web framework.
"""

from __future__ import annotations

from pathlib import Path

from autoexp.config.schema import DashboardConfig
from autoexp.state.layout import StateLayout

WEB_ROOT = Path(__file__).parent / "web"
MAX_BODY_BYTES = 4096
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def resolve_static(url_path: str, web_root: Path = WEB_ROOT) -> Path | None:
    """Map a URL path to a file under `web_root`, or None if outside/missing."""
    relative = "index.html" if url_path in ("", "/") else url_path.removeprefix("/static/")
    if relative == url_path:  # neither "/" nor under /static/
        return None
    root = web_root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    if candidate.suffix not in CONTENT_TYPES:
        return None
    return candidate


def serve(layout: StateLayout, config: DashboardConfig) -> None:
    """Run the server until interrupted."""
    raise NotImplementedError
