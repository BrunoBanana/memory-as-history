"""Zero-dependency HTTP layer for the AML Add/Search endpoints.

Uses ``http.server.ThreadingHTTPServer`` from the standard library: no extra
runtime dependencies, trivially dockerized, and adequate for the declared
evaluation concurrency. TLS termination is delegated to a reverse proxy
(see docs/aml/ for the deployment recipe).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .contract import ContractError, error_detail
from .memory_service import DEFAULT_DATA_DIR, MemoryService

MAX_BODY_BYTES = 100 * 1024 * 1024  # Add bodies may carry long transcripts
DEBUG = os.environ.get("AML_DEBUG") == "1"


class AMLHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    service: MemoryService | None = None  # assigned by make_handler

    # -- plumbing --------------------------------------------------------

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if length is None:
            raise ContractError("Content-Length header is required", 411)
        try:
            size = int(length)
        except ValueError:
            raise ContractError("invalid Content-Length header", 411)
        if size <= 0 or size > MAX_BODY_BYTES:
            raise ContractError(
                f"request body must be between 1 and {MAX_BODY_BYTES} bytes", 413
            )
        return self.rfile.read(size)

    def _parse_json_body(self) -> Any:
        raw = self._read_body()
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContractError("request body must be valid UTF-8 JSON", 400)

    def _authorized(self) -> bool:
        api_key = self.service.api_key if self.service else None
        if not api_key:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            presented = auth[7:]
        elif auth.startswith("Token "):
            presented = auth[6:]
        else:
            presented = self.headers.get("X-Api-Key", "")
        if not presented:
            return False
        return secrets.compare_digest(presented, api_key)

    def _handle_post(self, path: str) -> None:
        if not self._authorized():
            body, status = error_detail("invalid or missing API key", 401)
            self._json(status, body)
            return
        try:
            payload = self._parse_json_body()
            if path in ("/add",):
                result = self.service.add(payload)
            elif path in ("/search",):
                result = self.service.search(payload)
            else:
                body, status = error_detail("not found", 404)
                self._json(status, body)
                return
            self._json(200, result)
        except ContractError as exc:
            body, status = error_detail(exc.reason, exc.status)
            self._json(status, body)
        except Exception:
            # Never leak internals to the client; the platform retries 5xx
            # with backoff. With AML_DEBUG=1 the traceback goes to stderr.
            if DEBUG:
                traceback.print_exc(file=sys.stderr)
            body, status = error_detail("internal server error", 500)
            self._json(status, body)

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._json(200, {"status": "ok"})
        else:
            body, status = error_detail("not found", 404)
            self._json(status, body)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/add", "/search"):
            self._handle_post(path)
        else:
            body, status = error_detail("not found", 404)
            self._json(status, body)

    def log_message(self, format: str, *args: Any) -> None:  # quiet stdlib noise
        pass


def make_handler(service: MemoryService) -> Callable[..., AMLHandler]:
    """Bind a service to the handler class.

    ``BaseHTTPRequestHandler.__init__`` processes the request *during*
    construction (it calls ``self.handle()``), so the service must already be
    visible as a class attribute when the request arrives — an instance
    attribute set after ``AMLHandler(...)`` returns would be too late.
    """

    class Handler(AMLHandler):
        pass

    # Class bodies cannot see enclosing function variables, so bind the
    # service after class creation.
    Handler.service = service
    return Handler


def build_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    data_dir: str | None = None,
    api_key: str | None = None,
    search_mode: str = "lexical",
    semantic_min: float = 0.85,
    use_temporal: bool = True,
) -> ThreadingHTTPServer:
    """Build (but do not block on) the AML HTTP server. Tests use this."""
    service = MemoryService(
        data_dir=data_dir if data_dir is not None else DEFAULT_DATA_DIR,
        search_mode=search_mode,
        semantic_min=semantic_min,
        use_temporal=use_temporal,
    )
    service.api_key = api_key or None
    httpd = ThreadingHTTPServer((host, port), make_handler(service))
    httpd.daemon_threads = True
    return httpd


def serve(
    host: str = "0.0.0.0",
    port: int = 8000,
    data_dir: str | None = None,
    api_key: str | None = None,
    search_mode: str = "lexical",
    semantic_min: float = 0.85,
    use_temporal: bool = True,
) -> ThreadingHTTPServer:
    """Start the AML service and block forever."""
    httpd = build_server(host, port, data_dir, api_key, search_mode, semantic_min, use_temporal)
    httpd.serve_forever()
    return httpd
