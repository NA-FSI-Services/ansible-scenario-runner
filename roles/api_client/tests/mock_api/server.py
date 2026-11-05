#!/usr/bin/env python3
"""Minimal localhost mock REST API with Bearer auth (stdlib only)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Any
from urllib.parse import urlparse

HOST = os.environ.get("MOCK_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("MOCK_API_PORT", "8765"))
# Must match roles/api_client/tests/vault.example.yml → api_token
EXPECTED_TOKEN = os.environ.get("MOCK_API_TOKEN", "demo-token")


class MockHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer's reverse DNS lookup can delay startup on macOS.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "MockAPI/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _require_bearer(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            _json_response(self, 401, {"error": "missing_bearer", "detail": "Authorization: Bearer <token> required"})
            return False
        token = auth[len("Bearer ") :].strip()
        if token != EXPECTED_TOKEN:
            _json_response(self, 401, {"error": "invalid_token", "detail": "Bearer token rejected"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/health":
            _json_response(self, 200, {"status": "ok"})
            return
        if path == "/v1/message":
            if not self._require_bearer():
                return
            _json_response(self, 200, {"message": "Hello from mock API", "path": path})
            return
        _json_response(self, 404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/v1/echo":
            _json_response(self, 404, {"error": "not_found", "path": path})
            return
        if not self._require_bearer():
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _json_response(self, 400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "json_object_required"})
            return
        _json_response(self, 200, {"echo": payload, "message": payload.get("message", "echoed")})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', default='')
    args = parser.parse_args()
    httpd = MockHTTPServer((HOST, PORT), Handler)
    print(f"mock API listening on http://{HOST}:{PORT} (token from MOCK_API_TOKEN / default demo-token)", flush=True)
    try:
        ready_file = os.environ.get('MOCK_API_READY_FILE')
        if ready_file and args.instance:
            Path(ready_file).write_text(args.instance, encoding='utf-8')
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
