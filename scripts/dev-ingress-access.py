#!/usr/bin/env python3
"""Localhost access to the development Ingress.

Host ports 80 and 443 belong to the other Kind node. This process connects to
the development node's host port 80, where ingress-nginx is listening, and
presents store.local, dashboard.local, and gateway.local to that Ingress.

WordPress keeps absolute http://store.local links. The store listener rewrites
those to its own origin so a browser does not follow them back to port 80.

Binding 0.0.0.0 inside WSL is what Windows localhost forwarding accepts. The
listeners are not published on the LAN by this script. It does not print
secrets, cookies, or request bodies.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTEXT = "kind-urumi-k8s-dev"
STORE_PORT = 18080
DASHBOARD_PORT = 18081
GATEWAY_PORT = 18082
MAX_BODY = 20 * 1024 * 1024

HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def node_ip() -> str:
    proc = subprocess.run(
        ["kubectl", "--context", CONTEXT, "get", "nodes", "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    body = json.loads(proc.stdout)
    for node in body.get("items", []):
        for address in node.get("status", {}).get("addresses", []):
            if address.get("type") == "InternalIP" and address.get("address"):
                return address["address"]
    raise SystemExit("The development node has no InternalIP.")


def rewrite(body: bytes, source: bytes, target: bytes) -> bytes:
    body = re.sub(br"https?://store\.local(?::80(?!\d))?", target, body, flags=re.IGNORECASE)
    body = body.replace(br"http:\/\/store.local", br"http:\/\/127.0.0.1:" + source)
    body = body.replace(b"//store.local", b"//127.0.0.1:" + source)
    return body


class IngressProxy(BaseHTTPRequestHandler):
    upstream_host = "127.0.0.1"
    upstream_port = 80
    public_host = "store.local"
    rewrite_store = False
    public_port = STORE_PORT
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s %s\n" % (self.public_host, self.path.split("?", 1)[0]))

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_BODY:
            self.send_error(413)
            return
        payload = self.rfile.read(length) if length else b""
        if self.rewrite_store and payload:
            origin = f"http://127.0.0.1:{self.public_port}".encode("ascii")
            payload = payload.replace(origin, b"http://store.local")
        headers = {"Host": self.public_host, "Connection": "close"}
        for key, value in self.headers.items():
            if key.lower() in HOP or key.lower() in {"host", "accept-encoding"}:
                continue
            headers[key] = value
        headers["Accept-Encoding"] = "identity"
        try:
            conn = http.client.HTTPConnection(self.upstream_host, self.upstream_port, timeout=60)
            conn.request(self.command, self.path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read(MAX_BODY + 1)
        except OSError:
            self.send_error(502)
            return
        if len(raw) > MAX_BODY:
            self.send_error(502)
            return
        content_type = response.getheader("Content-Type", "")
        if self.rewrite_store and raw and content_type.split(";", 1)[0].strip().lower() in {
            "text/html",
            "text/css",
            "text/javascript",
            "application/javascript",
            "application/json",
            "application/xml",
            "text/xml",
        }:
            port = str(self.public_port).encode("ascii")
            origin = f"http://127.0.0.1:{self.public_port}".encode("ascii")
            raw = rewrite(raw, port, origin)
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() in HOP or key.lower() == "content-length":
                continue
            if self.rewrite_store and key.lower() in {"location", "refresh", "content-location", "link"}:
                value = value.replace("http://store.local", f"http://127.0.0.1:{self.public_port}")
                value = value.replace("https://store.local", f"http://127.0.0.1:{self.public_port}")
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        conn.close()

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def do_HEAD(self) -> None:
        self._serve()

    def do_PUT(self) -> None:
        self._serve()

    def do_DELETE(self) -> None:
        self._serve()


def handler_for(host: str, port: int, rewrite_store: bool, upstream: tuple[str, int]) -> type[IngressProxy]:
    class Bound(IngressProxy):
        pass

    Bound.upstream_host = upstream[0]
    Bound.upstream_port = upstream[1]
    Bound.public_host = host
    Bound.public_port = port
    Bound.rewrite_store = rewrite_store
    return Bound


def serve(port: int, handler: type[IngressProxy]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def self_test(upstream: tuple[str, int]) -> int:
    import urllib.request

    handler = handler_for("store.local", STORE_PORT, True, upstream)
    server = serve(STORE_PORT, handler)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{STORE_PORT}/product/urumi-baseline-storefront/", timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
            status = response.status
    finally:
        server.shutdown()
    visible = "URUMI-BASELINE-STOREFRONT" in body
    leaked = "http://store.local" in body
    print(f"self-test status={status} product_visible={visible} store_local_in_body={leaked}")
    return 0 if status == 200 and visible and not leaked else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    address = node_ip()
    upstream = (address, 80)
    if args.self_test:
        return self_test(upstream)
    servers = [
        serve(STORE_PORT, handler_for("store.local", STORE_PORT, True, upstream)),
        serve(DASHBOARD_PORT, handler_for("dashboard.local", DASHBOARD_PORT, False, upstream)),
        serve(GATEWAY_PORT, handler_for("gateway.local", GATEWAY_PORT, False, upstream)),
    ]
    print(f"store http://127.0.0.1:{STORE_PORT}/", flush=True)
    print(f"dashboard http://127.0.0.1:{DASHBOARD_PORT}/", flush=True)
    print(f"gateway http://127.0.0.1:{GATEWAY_PORT}/health", flush=True)
    print(f"ingress {address}:80", flush=True)

    def stop(_signum: int, _frame: object) -> None:
        for item in servers:
            item.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
