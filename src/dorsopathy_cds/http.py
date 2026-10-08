"""Local HTTP adapter for the research-only, source-linked rule service.

The adapter deliberately has no patient request/response logging. It is bound
to a loopback address and is not a deployment server for clinical use.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import socket
from typing import Any

from . import service
from .engine import DefinitionError, InputValidationError


MAX_BODY_BYTES = 64 * 1024
DEMO_HTML_PATH = Path(__file__).parent / "web" / "index.html"


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON numbers are not supported")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not supported")
        result[key] = value
    return result


class _LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # The stdlib default prints tracebacks. A local patient-data service
        # must not expose request data or exceptions through that side channel.
        return


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DorsopathyCDS"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: Any) -> None:
        # Even request paths can contain identifying values. No access log.
        return

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._response(status, body, "application/json; charset=utf-8")

    def _html_response(self) -> None:
        # Only the packaged static page is served. Case data is never
        # interpolated into HTML by this adapter.
        body = DEMO_HTML_PATH.read_text(encoding="utf-8").encode("utf-8")
        self._response(200, body, "text/html; charset=utf-8")

    def _response(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json_response(status, {"error": {"code": code, "message": message}})

    def _internal_error(self) -> None:
        self._error(500, "internal_error", "The request could not be processed.")

    def do_GET(self) -> None:
        try:
            if self.path in ("/", "/demo"):
                self._html_response()
            elif self.path == "/health":
                self._json_response(200, {"status": "ok", "mode": "research_only"})
            elif self.path == "/rules":
                rules, sources = service.load_catalog()
                self._json_response(200, {"rules": rules, "sources": sources})
            else:
                self._error(404, "not_found", "The requested endpoint does not exist.")
        except Exception:
            self._internal_error()

    def do_POST(self) -> None:
        if self.path != "/evaluate":
            self._error(404, "not_found", "The requested endpoint does not exist.")
            return

        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            self._error(415, "unsupported_media_type", "Content-Type must be application/json.")
            return
        if self.headers.get_all("Transfer-Encoding"):
            self._error(400, "invalid_request", "Transfer-Encoding is not supported.")
            return

        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            self._error(411, "length_required", "Content-Length is required.")
            return
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self._error(400, "invalid_request", "Content-Length must be one non-negative integer.")
            return
        try:
            length = int(lengths[0])
        except ValueError:
            self._error(400, "invalid_request", "Content-Length must be one non-negative integer.")
            return
        if length > MAX_BODY_BYTES:
            self._error(413, "payload_too_large", "The request body exceeds the 64 KiB limit.")
            return

        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Incomplete request body")
            payload = json.loads(
                body.decode("utf-8"),
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except (ValueError, UnicodeError, OSError, RecursionError):
            self._error(400, "invalid_json", "The request body must be complete, unambiguous UTF-8 JSON.")
            return

        if not isinstance(payload, dict):
            self._error(400, "invalid_case", "The request body must be a JSON object.")
            return
        try:
            report = service.evaluate_case(payload)
            self._json_response(200, report)
        except (service.PayloadError, InputValidationError):
            self._error(400, "invalid_case", "The case does not satisfy the input schema.")
        except DefinitionError:
            self._internal_error()
        except Exception:
            self._internal_error()


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Create an unstarted, loopback-only server (port 0 is useful for tests)."""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("The local prototype requires a loopback IP address") from exc
    if not address.is_loopback:
        raise ValueError("The local prototype requires a loopback IP address")
    server_class = _LocalHTTPServer
    if address.version == 6:
        class _IPv6LocalHTTPServer(_LocalHTTPServer):
            address_family = socket.AF_INET6

        server_class = _IPv6LocalHTTPServer
    return server_class((host, port), _Handler)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve locally until interrupted, closing the listening socket on exit."""
    with create_server(host, port) as server:
        bound_host, bound_port = server.server_address[:2]
        url_host = f"[{bound_host}]" if ":" in bound_host else bound_host
        print(f"Локальная демоверсия: http://{url_host}:{bound_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
