"""Command line: ``faicam [cameras.yaml]`` runs faistream and the web page together."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from typing import Any

from faistream.config import ConfigError
from faistream.streaming.service import Service, ServiceError

from faicam import __version__
from faicam.config import ConfigFile

log = logging.getLogger("faicam")

DEFAULT_CONFIG = "cameras.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Exit cleanly on SIGTERM (docker stop, systemd), also when running as PID 1.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        _serve(args)
        return 0
    except KeyboardInterrupt:
        return 130
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="faicam",
        description="Manage cameras from a web page. faistream reads each camera once and "
        "shares it with every viewer and program.",
        epilog="examples:\n  faicam\n  faicam ~/cameras.yaml --network",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=DEFAULT_CONFIG,
        help=f"settings file (default: {DEFAULT_CONFIG}; created when you add a camera)",
    )
    parser.add_argument("--port", type=int, help="faistream's control port (default 7300)")
    parser.add_argument(
        "--network", action="store_true", help="also share the cameras with programs elsewhere"
    )
    parser.add_argument("--version", action="version", version=f"faicam {__version__}")
    parser.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"]
    )
    return parser


def _serve(args: argparse.Namespace) -> None:
    from faicam.server import WebUI

    file = ConfigFile.open(args.config)
    overrides: dict[str, Any] = {}
    if args.port:
        overrides["port"] = args.port
    if args.network:
        overrides["network"] = True
    file.overrides = overrides
    config = file.config()
    if config.ui is None:
        raise ConfigError(
            "the web page is turned off (ui: false). "
            "To run the cameras without it, use `faistream serve`."
        )
    service = Service(config)
    restart = threading.Event()

    def request_restart() -> None:
        restart.set()
        service.request_stop()

    ui = WebUI(service, config.ui, file, restart=request_restart)
    service.serve_forever(ui, _Banner(service, ui, file))
    if restart.is_set():
        # Settings like ports and sharing only take effect on start. Replace this
        # process with a fresh one (same PID, same terminal) that reads them.
        print("\nRestarting faicam to apply the new settings...", flush=True)
        os.execv(sys.executable, [sys.executable, "-m", "faicam", *sys.argv[1:]])


class _Banner:
    """Tells the user, once everything has started, where to go next."""

    def __init__(self, service: Service, ui: Any, file: ConfigFile):
        self.service, self.ui, self.file = service, ui, file

    def start(self) -> None:
        from faicam.editor import links_url

        names = list(self.service.config.streams)
        count = f" with {len(names)} camera{'s' if len(names) != 1 else ''}" if names else ""
        what = "watch and manage your cameras" if names else "add your first camera"
        lines = [f"faicam is running{count}.", f"Open {self.ui.url} to {what}."]
        if self.ui.ui.links == "network" and names:
            example = f"{links_url(self.ui.ui)}/live/{names[0]}.mjpg"
            lines.append(f"Other devices can watch camera links, like {example}")
        lines += [f"Settings: {self.file.path.resolve()}", "Press Ctrl+C to stop."]
        print("\n" + "\n".join(lines) + "\n", flush=True)

    def stop(self) -> None:
        pass


if __name__ == "__main__":
    sys.exit(main())
