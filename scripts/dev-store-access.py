#!/usr/bin/env python3
"""Local browser access to the development WordPress Service.

Starts one kubectl port-forward to svc/urumi-wordpress and a reverse proxy in
front of it. The proxy sends Host: store.local upstream, does not follow
redirects, and rewrites canonical store.local URLs so the browser stays on
http://127.0.0.1:<proxy-port>/.

A listener bound only to 127.0.0.1 inside WSL is visible to Windows but does
not accept connections from the Windows browser. Binding 0.0.0.0 is the
narrowest bind that Windows localhost forwarding accepts. Windows wslrelay
publishes that port on 127.0.0.1. This script does not read or print passwords,
cookies, or request bodies.
"""

from __future__ import annotations

import gzip
import http.client
import http.server
import os
import re
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterable

CONTEXT = "kind-urumi-k8s-dev"
NAMESPACE = "default"
SERVICE = "svc/urumi-wordpress"
CANONICAL_ORIGIN = "http://store.local"
CANONICAL_HOST = "store.local"
PREFERRED_PROXY_PORT = 18082
PREFERRED_BACKEND_PORT = 18083
PORT_SEARCH_SPAN = 40
MAX_BODY_BYTES = 20 * 1024 * 1024
UPSTREAM_TIMEOUT_SECONDS = 60

HOP_BY_HOP = {
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

REWRITE_CONTENT_TYPES = {
    "application/javascript",
    "application/json",
    "application/ld+json",
    "application/xhtml+xml",
    "application/xml",
    "application/x-javascript",
    "text/css",
    "text/html",
    "text/javascript",
    "text/plain",
    "text/xml",
}

REQUEST_REWRITE_PREFIXES = (
    "application/json",
    "application/ld+json",
    "application/x-www-form-urlencoded",
    "application/xml",
    "text/",
)


class ProxyFailure(Exception):
    """Upstream request could not be completed."""


def can_bind(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def first_free_port(start: int, host: str, avoid: set[int]) -> int:
    for port in range(start, start + PORT_SEARCH_SPAN):
        if port in avoid:
            continue
        if can_bind(host, port):
            return port
    raise SystemExit(f"No free TCP port on {host} in {start}-{start + PORT_SEARCH_SPAN - 1}.")


def windows_browser_can_connect(port: int) -> bool:
    """Return whether Windows localhost can complete a connection to this WSL port.

    A 127.0.0.1 bind inside WSL does not accept Windows browser connections.
    Some Windows listeners also occupy 127.0.0.1:<port> without forwarding into
    WSL. This probe binds 0.0.0.0 only for the duration of one local request.
    """
    curl = "/mnt/c/Windows/System32/curl.exe"
    if not os.path.isfile(curl):
        print("Windows curl.exe was not found; the Windows reachability probe was skipped.", flush=True)
        return True
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        return False
    sock.listen(1)
    sock.settimeout(4)
    process = subprocess.Popen(
        [curl, "-sS", "--max-time", "3", "-o", "NUL", "-w", "%{http_code}", f"http://127.0.0.1:{port}/"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        connection, _addr = sock.accept()
    except socket.timeout:
        process.kill()
        process.wait(timeout=3)
        return False
    try:
        connection.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        connection.close()
    except OSError:
        process.kill()
        process.wait(timeout=3)
        return False
    finally:
        sock.close()
    try:
        output, _error = process.communicate(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
        return False
    return output.strip() == "204"


def choose_ports() -> tuple[int, int]:
    avoided: set[int] = set()
    proxy_port: int | None = None
    search_from = PREFERRED_PROXY_PORT
    while search_from < PREFERRED_PROXY_PORT + PORT_SEARCH_SPAN:
        candidate = first_free_port(search_from, "0.0.0.0", avoided)
        if windows_browser_can_connect(candidate):
            proxy_port = candidate
            break
        print(f"Skipping proxy port {candidate}: Windows localhost did not connect.", flush=True)
        avoided.add(candidate)
        search_from = candidate + 1
    if proxy_port is None:
        raise SystemExit("No Windows-reachable local proxy port was found.")
    backend_port = first_free_port(PREFERRED_BACKEND_PORT, "127.0.0.1", avoided | {proxy_port})
    return proxy_port, backend_port


def public_origin(proxy_port: int) -> str:
    return f"http://127.0.0.1:{proxy_port}"


def _encoded(url: str) -> bytes:
    return urllib.parse.quote(url, safe="").encode("ascii")


class UrlRewriter:
    """Translate store.local and the local proxy origin in both directions."""

    def __init__(self, proxy_port: int) -> None:
        self.proxy_port = proxy_port
        self.origin = public_origin(proxy_port)
        origin_b = self.origin.encode("ascii")
        canonical_b = CANONICAL_ORIGIN.encode("ascii")
        port_b = str(proxy_port).encode("ascii")
        encoded_origin = _encoded(self.origin)
        encoded_canonical = _encoded(CANONICAL_ORIGIN)
        # PHP json_encode escapes slashes, then rawurlencode. The result is
        # http%3A%5C%2F%5C%2Fstore.local, which the plain percent-encoding rule misses.
        encoded_escaped_origin = b"http%3A%5C%2F%5C%2F127.0.0.1%3A" + port_b
        encoded_escaped_canonical = b"http%3A%5C%2F%5C%2Fstore.local"

        self._downstream = (
            (re.compile(br"https?://store\.local(?::80(?!\d))?", re.IGNORECASE), origin_b),
            (re.compile(br"https?:\\/\\/store\.local(?::80(?!\d))?", re.IGNORECASE), br"http:\/\/127.0.0.1:" + port_b),
            (re.compile(br"https?%3a(?:%2f){2}store\.local", re.IGNORECASE), encoded_origin),
            (re.compile(br"https?%3a%5c%2f%5c%2fstore\.local", re.IGNORECASE), encoded_escaped_origin),
            (re.compile(br"//store\.local(?::80(?!\d))?", re.IGNORECASE), b"//127.0.0.1:" + port_b),
            (re.compile(br"http://127\.0\.0\.1(?::80(?!\d))?(?=/|\?|#|$)", re.IGNORECASE), origin_b),
        )
        self._upstream = (
            (re.compile(br"https?://127\.0\.0\.1:" + port_b + br"(?!\d)", re.IGNORECASE), canonical_b),
            (re.compile(br"https?:\\/\\/127\.0\.0\.1:" + port_b + br"(?!\d)", re.IGNORECASE), br"http:\/\/store.local"),
            (re.compile(br"https?%3a(?:%2f){2}127\.0\.0\.1%3a" + port_b + br"(?!\d)", re.IGNORECASE), encoded_canonical),
            (re.compile(br"https?%3a%5c%2f%5c%2f127\.0\.0\.1%3a" + port_b + br"(?!\d)", re.IGNORECASE), encoded_escaped_canonical),
            (re.compile(br"//127\.0\.0\.1:" + port_b + br"(?!\d)", re.IGNORECASE), b"//store.local"),
        )

    def downstream(self, payload: bytes) -> bytes:
        return _apply(self._downstream, payload)

    def upstream(self, payload: bytes) -> bytes:
        return _apply(self._upstream, payload)

    def downstream_text(self, value: str) -> str:
        return self.downstream(value.encode("utf-8", "surrogateescape")).decode("utf-8", "surrogateescape")

    def upstream_text(self, value: str) -> str:
        return self.upstream(value.encode("utf-8", "surrogateescape")).decode("utf-8", "surrogateescape")


def _apply(rules: Iterable[tuple[re.Pattern[bytes], bytes]], payload: bytes) -> bytes:
    for pattern, replacement in rules:
        payload = pattern.sub(replacement, payload)
    return payload


def content_type_base(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def should_rewrite_response(content_type: str) -> bool:
    base = content_type_base(content_type)
    if base in REWRITE_CONTENT_TYPES or base.startswith("text/"):
        return True
    return base.endswith("+json") or base.endswith("+xml")


def should_rewrite_request(content_type: str) -> bool:
    base = content_type_base(content_type)
    return any(base.startswith(prefix) for prefix in REQUEST_REWRITE_PREFIXES)


def sanitize_set_cookie(value: str) -> str:
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if not parts:
        return value
    kept = [parts[0]]
    for attribute in parts[1:]:
        name, separator, domain = attribute.partition("=")
        if name.strip().lower() == "domain" and separator:
            host = domain.strip().lstrip(".").lower()
            if host == CANONICAL_HOST:
                continue
        kept.append(attribute)
    return "; ".join(kept)


def read_chunked(stream: socket.SocketIO) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        line = stream.readline(64 * 1024)
        if not line:
            raise ProxyFailure("incomplete chunked body")
        size_token = line.split(b";", 1)[0].strip()
        try:
            size = int(size_token, 16)
        except ValueError as exc:
            raise ProxyFailure("invalid chunk size") from exc
        if size < 0 or total + size > MAX_BODY_BYTES:
            raise ProxyFailure("request body is too large")
        if size == 0:
            while True:
                trailer = stream.readline(64 * 1024)
                if trailer in (b"\r\n", b"\n", b""):
                    break
            return b"".join(chunks)
        data = stream.read(size)
        if len(data) != size:
            raise ProxyFailure("incomplete chunked body")
        chunks.append(data)
        total += size
        delimiter = stream.read(2)
        if delimiter not in (b"\r\n", b"\n"):
            raise ProxyFailure("invalid chunk delimiter")


def decompress_if_needed(headers: list[tuple[str, str]], body: bytes) -> tuple[bytes, bool]:
    encoding = ""
    for name, value in headers:
        if name.lower() == "content-encoding":
            encoding = value.strip().lower()
    if encoding in ("", "identity"):
        return body, False
    if encoding == "gzip":
        try:
            return gzip.decompress(body), True
        except OSError as exc:
            raise ProxyFailure("upstream gzip response could not be decoded") from exc
    raise ProxyFailure("upstream content encoding is not supported")


class StoreProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "urumi-dev-store-proxy"
    close_connection = True

    def setup(self) -> None:
        super().setup()
        self._response_started = False

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        path = self.path.split("?", 1)[0]
        sys.stderr.write(f"{self.command} {path} {code}\n")

    def do_GET(self) -> None:
        self._proxy()

    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET
    do_HEAD = do_GET

    def _proxy(self) -> None:
        try:
            self._forward()
        except ProxyFailure as exc:
            self._send_gateway_error(str(exc))
        except (TimeoutError, OSError, http.client.HTTPException):
            self._send_gateway_error("upstream connection failed")

    def _send_gateway_error(self, message: str) -> None:
        if self._response_started:
            return
        body = message.encode("ascii", "replace")
        self.send_response(502)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.log_request(502)

    def _read_request_body(self) -> bytes:
        if self.command == "HEAD":
            return b""
        declared = self.headers.get("Content-Length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError as exc:
                raise ProxyFailure("invalid content length") from exc
            if length < 0 or length > MAX_BODY_BYTES:
                raise ProxyFailure("request body is too large")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ProxyFailure("incomplete request body")
            return body
        transfer = self.headers.get("Transfer-Encoding", "")
        if "chunked" in transfer.lower():
            return read_chunked(self.rfile)
        return b""

    def _forward(self) -> None:
        rewriter: UrlRewriter = self.server.rewriter  # type: ignore[attr-defined]
        backend_port: int = self.server.backend_port  # type: ignore[attr-defined]
        body = self._read_request_body()
        content_type = self.headers.get("Content-Type", "")
        if body and (not content_type or should_rewrite_request(content_type)):
            body = rewriter.upstream(body)
        target = rewriter.upstream_text(self.path)
        if not target.startswith("/"):
            raise ProxyFailure("invalid request target")

        outbound = []
        for name, value in self.headers.items():
            lower = name.lower()
            if lower in HOP_BY_HOP or lower in {"host", "content-length", "accept-encoding"}:
                continue
            if lower in {"referer", "origin"}:
                value = rewriter.upstream_text(value)
            outbound.append((name, value))
        outbound.append(("Host", CANONICAL_HOST))
        outbound.append(("Connection", "close"))
        if self.command != "HEAD":
            outbound.append(("Content-Length", str(len(body))))

        connection = http.client.HTTPConnection("127.0.0.1", backend_port, timeout=UPSTREAM_TIMEOUT_SECONDS)
        try:
            connection.request(self.command, target, body=body if self.command != "HEAD" else None, headers=dict(outbound))
            upstream = connection.getresponse()
            raw_body = b"" if self.command == "HEAD" else upstream.read(MAX_BODY_BYTES + 1)
            header_pairs = upstream.getheaders()
            status = upstream.status
        finally:
            connection.close()

        if len(raw_body) > MAX_BODY_BYTES:
            raise ProxyFailure("upstream response is too large")

        response_type = ""
        for name, value in header_pairs:
            if name.lower() == "content-type":
                response_type = value
        decoded, _decoded_gzip = decompress_if_needed(header_pairs, raw_body)
        rewritten = False
        if self.command != "HEAD" and should_rewrite_response(response_type):
            decoded = rewriter.downstream(decoded)
            rewritten = True

        self._response_started = True
        self.send_response(status)
        skip = HOP_BY_HOP | {"content-length", "date", "server"}
        if rewritten:
            skip.add("content-encoding")
        for name, value in header_pairs:
            lower = name.lower()
            if lower in skip:
                continue
            if lower in {"location", "content-location", "refresh", "link"}:
                value = rewriter.downstream_text(value)
            elif lower == "set-cookie":
                value = sanitize_set_cookie(value)
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(decoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(decoded)
        self.log_request(status)


class StoreProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    address_family = socket.AF_INET
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, proxy_port: int, backend_port: int) -> None:
        self.backend_port = backend_port
        self.rewriter = UrlRewriter(proxy_port)
        super().__init__(("0.0.0.0", proxy_port), StoreProxyHandler)


def start_port_forward(backend_port: int) -> subprocess.Popen[str]:
    command = [
        "kubectl",
        "--context",
        CONTEXT,
        "-n",
        NAMESPACE,
        "port-forward",
        "--address",
        "127.0.0.1",
        SERVICE,
        f"{backend_port}:80",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    ready = threading.Event()
    errors: list[str] = []

    def drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            text = line.strip()
            if "Forwarding from" in text:
                ready.set()
            elif text:
                errors.append(text)
                if process.poll() is not None:
                    ready.set()
        ready.set()

    threading.Thread(target=drain, name="port-forward-log", daemon=True).start()
    if not ready.wait(20) or process.poll() is not None:
        stop_port_forward(process)
        detail = errors[-1] if errors else "port-forward did not become ready"
        raise SystemExit(f"Could not start the WordPress port-forward. {detail}")
    return process


def stop_port_forward(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)


def self_test() -> None:
    rewriter = UrlRewriter(18082)
    login = (
        b'<form action="http://store.local/wp-login.php?redirect_to='
        b'http%3A%2F%2Fstore.local%2Fwp-admin%2F">'
    )
    rewritten = rewriter.downstream(login)
    if b"store.local" in rewritten:
        raise SystemExit("downstream rewrite left store.local in place")
    expected = (
        b'<form action="http://127.0.0.1:18082/wp-login.php?redirect_to='
        b'http%3A%2F%2F127.0.0.1%3A18082%2Fwp-admin%2F">'
    )
    if rewritten != expected:
        raise SystemExit("downstream login rewrite mismatch")
    if rewriter.upstream(rewritten) != login.replace(b"http%3A%2F%2Fstore.local", b"http%3A%2F%2Fstore.local"):
        restored = rewriter.upstream(rewritten)
        if b"http://store.local/wp-login.php" not in restored or b"store.local%2Fwp-admin" not in restored:
            raise SystemExit("upstream restore mismatch")
    escaped = rewriter.downstream(b'"http:\\/\\/store.local\\/wp-admin\\/"')
    if escaped != b'"http:\\/\\/127.0.0.1:18082\\/wp-admin\\/"':
        raise SystemExit("escaped JSON rewrite mismatch")
    encoded_settings = (
        b"var wcSettings = JSON.parse( decodeURIComponent( "
        b"'%22http%3A%5C%2F%5C%2Fstore.local%5C%2Fcheckout%5C%2F%22' ) );"
    )
    encoded_expected = (
        b"var wcSettings = JSON.parse( decodeURIComponent( "
        b"'%22http%3A%5C%2F%5C%2F127.0.0.1%3A18082%5C%2Fcheckout%5C%2F%22' ) );"
    )
    encoded_rewritten = rewriter.downstream(encoded_settings)
    if encoded_rewritten != encoded_expected:
        raise SystemExit("encoded WooCommerce settings rewrite mismatch")
    if rewriter.upstream(encoded_rewritten) != encoded_settings:
        raise SystemExit("encoded WooCommerce settings upstream restore mismatch")
    encoded_https = rewriter.downstream(b"https%3a%5c%2f%5c%2fstore.local%5C%2Fcheckout%5C%2F")
    if encoded_https != b"http%3A%5C%2F%5C%2F127.0.0.1%3A18082%5C%2Fcheckout%5C%2F":
        raise SystemExit("encoded https WooCommerce settings rewrite mismatch")
    if rewriter.downstream(b"//store.local/a.js") != b"//127.0.0.1:18082/a.js":
        raise SystemExit("protocol-relative rewrite mismatch")
    if rewriter.downstream(b"http://127.0.0.1/wp-admin/") != b"http://127.0.0.1:18082/wp-admin/":
        raise SystemExit("bare loopback redirect was not rewritten")
    if rewriter.downstream(b"http://127.0.0.1:18082/wp-admin/") != b"http://127.0.0.1:18082/wp-admin/":
        raise SystemExit("proxy origin was rewritten twice")
    if rewriter.downstream(b"https://wordpress.org/") != b"https://wordpress.org/":
        raise SystemExit("unrelated host was rewritten")
    compressed = gzip.compress(login)
    if gzip.decompress(compressed) != login:
        raise SystemExit("gzip round trip failed")
    cookie = sanitize_set_cookie("wordpress_test_cookie=WP+Cookie+check; path=/; Domain=store.local; HttpOnly")
    if cookie != "wordpress_test_cookie=WP+Cookie+check; path=/; HttpOnly":
        raise SystemExit("store.local cookie domain was not removed")
    other = sanitize_set_cookie("a=b; Domain=example.com")
    if other != "a=b; Domain=example.com":
        raise SystemExit("unrelated cookie domain was removed")
    print("self-test ok")


def serve(proxy_port: int, backend_port: int, process: subprocess.Popen[str]) -> int:
    stop = threading.Event()

    def on_signal(signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    server = StoreProxyServer(proxy_port, backend_port)
    thread = threading.Thread(target=server.serve_forever, name="store-proxy", daemon=True)
    thread.start()
    origin = public_origin(proxy_port)
    print(f"Browser URL: {origin}/wp-login.php", flush=True)
    print(f"Port-forward 127.0.0.1:{backend_port} -> {SERVICE}:80", flush=True)
    print("Press Ctrl-C to stop.", flush=True)
    exit_code = 0
    try:
        while not stop.wait(0.5):
            if process.poll() is not None:
                print("Port-forward exited.", file=sys.stderr, flush=True)
                exit_code = 1
                break
    finally:
        server.shutdown()
        server.server_close()
    return exit_code


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        self_test()
        return 0
    proxy_port, backend_port = choose_ports()
    if proxy_port != PREFERRED_PROXY_PORT or backend_port != PREFERRED_BACKEND_PORT:
        print(
            f"Using proxy port {proxy_port} and backend port {backend_port}.",
            flush=True,
        )
    process = start_port_forward(backend_port)
    try:
        return serve(proxy_port, backend_port, process)
    finally:
        stop_port_forward(process)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(0)
