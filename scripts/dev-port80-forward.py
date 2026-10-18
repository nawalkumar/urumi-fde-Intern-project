#!/usr/bin/env python3
"""Forward the test Kind node's published port 80 to the dev node's Ingress.

The dev node is not published on host port 80. urumi-k8s-test-control-plane
owns that publish and has nothing listening, so http://store.local is reset.
Run this inside that node's network namespace. It does not change either
cluster's CNI, NetworkPolicy, or port publish.
"""

import socket
import sys
import threading

def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def handle(client: socket.socket, upstream: tuple[str, int]) -> None:
    try:
        remote = socket.create_connection(upstream, timeout=10)
    except OSError:
        client.close()
        return
    client.settimeout(None)
    threading.Thread(target=pipe, args=(client, remote), daemon=True).start()
    pipe(remote, client)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: dev-port80-forward.py <dev-node-ip> <port>")
    upstream = (sys.argv[1], int(sys.argv[2]))
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", upstream[1]))
    server.listen(64)
    while True:
        client, _addr = server.accept()
        threading.Thread(target=handle, args=(client, upstream), daemon=True).start()


if __name__ == "__main__":
    main()
