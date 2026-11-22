"""Dashboard HTTP server: JSON API + static frontend, bound to loopback.

- `/` and `/static/...` serve files from `dashboard/web/` (index.html, js/,
  css/). `resolve_static()` refuses anything outside that directory.
- `/api/...` is dispatched through `routes.match()` to `handlers.py` and
  wrapped in the envelope from `schemas.py`.
- Every request must carry a loopback `Host` header naming the server's own
  port (403 `forbidden_host` otherwise), which blocks DNS rebinding.
- POSTs must carry `Content-Type: application/json` and an `Origin` equal to
  the server's own origin (403 `forbidden_origin` otherwise); bodies are capped
  at 4 KiB and validated before a handler runs.
- `/api/stream` holds the connection open for server-sent events.

Built on `http.server.ThreadingHTTPServer`; no third-party web framework.
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from autoexp.config.schema import DashboardConfig
from autoexp.dashboard import handlers
from autoexp.dashboard.routes import Match, match
from autoexp.state.layout import StateLayout

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).parent / "web"
MAX_BODY_BYTES = 4096
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
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


def allowed_hosts(host: str, port: int) -> frozenset[str]:
    return frozenset(f"{h}:{port}" for h in {host, "127.0.0.1", "localhost"})


def allowed_origins(host: str, port: int) -> frozenset[str]:
    return frozenset(f"http://{h}" for h in allowed_hosts(host, port))


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, layout: StateLayout, config: DashboardConfig) -> None:
        self.layout = layout
        self.stopping = threading.Event()
        super().__init__((config.host, config.port), _Handler)
        self.port = int(self.server_address[1])  # resolved when config.port is 0
        self.hosts = allowed_hosts(config.host, self.port)
        self.origins = allowed_origins(config.host, self.port)


class _Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.debug("dashboard: " + format, *args)

    # ---------------------------------------------------------- responses

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any = None, error: tuple[str, str] | None = None) -> None:
        envelope = {
            "ok": error is None,
            "data": data if error is None else None,
            "error": {"code": error[0], "message": error[1]} if error else None,
        }
        self._send(status, json.dumps(envelope, default=str).encode(), "application/json")

    # ------------------------------------------------------------ methods

    def _host_allowed(self) -> bool:
        if self.headers.get("Host") in self.server.hosts:
            return True
        self._json(403, error=("forbidden_host", "unexpected Host header"))
        return False

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        if not self._host_allowed():
            return
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            path = resolve_static(url.path)
            if path is None:
                self._json(404, error=("not_found", "no such page"))
                return
            self._send(200, path.read_bytes(), CONTENT_TYPES[path.suffix])
            return
        found = match("GET", url.path)
        if found is None:
            self._json(404, error=("not_found", f"no route GET {url.path}"))
            return
        if found.route.handler == "stream":
            self._stream()
            return
        self._dispatch(found, dict(parse_qsl(url.query)), None)

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        if not self._host_allowed():
            return
        url = urlsplit(self.path)
        found = match("POST", url.path)
        if found is None:
            self._json(404, error=("not_found", f"no route POST {url.path}"))
            return
        if self.headers.get("Origin") not in self.server.origins:
            self._json(403, error=("forbidden_origin", "cross-origin requests are refused"))
            return
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._json(415, error=("bad_request", "Content-Type must be application/json"))
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(413, error=("bad_request", "body too large"))
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, error=("bad_request", "body is not JSON"))
            return
        if not isinstance(body, dict):
            self._json(400, error=("bad_request", "body must be an object"))
            return
        self._dispatch(found, {}, body)

    def _dispatch(self, found: Match, query: dict[str, str], body: dict[str, Any] | None) -> None:
        layout = self.server.layout
        name = found.route.handler
        params = found.params
        try:
            if name == "get_overview":
                data = handlers.get_overview(layout)
            elif name == "get_run":
                data = handlers.get_run(layout, params["run_id"])
            elif name == "get_curve":
                data = handlers.get_curve(layout, params["run_id"], query)
            elif name == "get_events":
                data = handlers.get_events(layout, query)
            elif name == "get_pane":
                data = handlers.get_pane(layout, params["worker_id"], query)
            elif name == "answer_approval":
                data = handlers.answer_approval(layout, params["approval_id"], body)  # type: ignore[arg-type]
            elif name == "set_takeover":
                data = handlers.set_takeover(layout, params["worker_id"], body)  # type: ignore[arg-type]
            elif name == "request_stop":
                data = handlers.request_stop(layout)
            else:
                self._json(404, error=("not_found", name))
                return
        except handlers.ApiError as err:
            self._json(err.status, error=(err.code, str(err)))
            return
        except Exception:
            log.exception("dashboard handler %s failed", name)
            self._json(500, error=("internal", "internal error; see the dashboard log"))
            return
        self._json(HTTPStatus.OK, data)

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for chunk in handlers.stream(
                self.server.layout, lambda: not self.server.stopping.is_set()
            ):
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def start(layout: StateLayout, config: DashboardConfig) -> DashboardServer:
    """Start serving in a daemon thread and return the server (call .shutdown())."""
    server = DashboardServer(layout, config)
    thread = threading.Thread(target=server.serve_forever, name="dashboard", daemon=True)
    thread.start()
    return server


def stop(server: DashboardServer) -> None:
    server.stopping.set()
    server.shutdown()
    server.server_close()


def serve(layout: StateLayout, config: DashboardConfig) -> None:
    """Run the server until interrupted."""
    server = DashboardServer(layout, config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.server_close()
