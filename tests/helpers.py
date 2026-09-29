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
