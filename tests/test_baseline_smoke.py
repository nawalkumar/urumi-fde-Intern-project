"""Local HTTP checks for scripts/baseline_smoke.py. No cluster client is used."""

from __future__ import annotations

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baseline_smoke", ROOT / "scripts" / "baseline_smoke.py")
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b'{"status":"ok"}'
        elif self.path == "/":
            body = b"<html>urumi</html>"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_missing_targets_fail() -> None:
    assert smoke.main([]) == 2


def test_matching_local_server_passes() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert smoke.main(["--gateway", origin, "--dashboard", origin, "--store", origin]) == 0
    finally:
        server.shutdown()


def test_refused_port_fails() -> None:
    assert smoke.main(["--gateway", "http://127.0.0.1:9"]) == 1


def test_wrong_body_fails() -> None:
    class Empty(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Empty)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert smoke.main(["--gateway", origin]) == 1
    finally:
        server.shutdown()
