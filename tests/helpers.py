"""Shared by the tests."""

import socket


def free_port_block(size: int = 4) -> int:
    """A port p such that p .. p+size-1 are all free."""
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            base: int = s.getsockname()[1]
        if base + size > 65535:
            continue
        try:
            for offset in range(size):
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", base + offset))
            return base
        except OSError:
            continue
    raise RuntimeError("no free port block")


_handed_out: set[int] = set()


def _free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def stream_port() -> int:
    """A new port p for a camera's stream_port, with p and its local port p + 100 free.

    From a range the OS doesn't hand out on its own, and never the same twice, so the
    cameras of one test can't clash with each other or with the service's other sockets.
    """
    import random

    while True:
        port = random.randrange(30000, 39000)
        ports = {port, port + 100}
        if not ports & _handed_out and all(_free(p) for p in ports):
            _handed_out.update(ports)
            return port
