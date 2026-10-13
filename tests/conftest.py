"""Start ASGI MCP services on ephemeral local ports."""

import socket
import threading
import time
from collections.abc import Callable, Iterator

import pytest
import uvicorn
from fastapi import FastAPI


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


def start_app(app: FastAPI) -> _Server:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = _Server(config)
    server.bound_port = port  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_open(port)
    return server


def _wait_until_open(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError(f"Nothing accepted connections on 127.0.0.1:{port}.")


@pytest.fixture
def launch_app() -> Iterator[Callable[[FastAPI], str]]:
    servers: list[_Server] = []

    def launch(app: FastAPI) -> str:
        server = start_app(app)
        servers.append(server)
        return f"http://127.0.0.1:{server.bound_port}"  # type: ignore[attr-defined]

    yield launch
    for server in servers:
        server.should_exit = True
