"""The numbers behind the stats page (``/stats``).

Every 2 seconds the web page's recorder takes a sample: each camera's health,
who is watching it, and this process's CPU, memory and network use. The last
hour of samples is kept in memory for the charts; nothing is written to disk.
"""

from __future__ import annotations

import itertools
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from faistream.streaming.service import Service

log = logging.getLogger(__name__)

INTERVAL = 2.0  # seconds between samples
HISTORY = 1800  # samples kept: one hour
_PS_EVERY = 10.0  # macOS memory comes from `ps`; don't run it on every sample


@dataclass
class WebView:
    """Someone watching a camera through its web link (``/live/<name>.mjpg``)."""

    camera: str
    address: str
    app: str
    panel: bool  # the control panel's own live view
    since: float = field(default_factory=time.time)
    bytes: int = 0


class Stats:
    def __init__(self, service: Service) -> None:
        self._service = service
        self._views: dict[int, WebView] = {}
        self._ids = itertools.count(1)
        self._ended_bytes: dict[str, int] = {}  # sent to web views that have closed
        self._samples: deque[dict[str, Any]] = deque(maxlen=HISTORY)
        self._current: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._process = _ProcessUsage()
        # per camera: (thread CPU seconds, bytes sent to machines, bytes sent to web views)
        self._counters: dict[str, tuple[float, int, int]] = {}
        self._sampled = time.monotonic()

    # -- web views (kept by the page's /live handler) ------------------------

    def open_view(self, view: WebView) -> int:
        with self._lock:
            view_id = next(self._ids)
            self._views[view_id] = view
        return view_id

    def close_view(self, view_id: int) -> None:
        with self._lock:
            view = self._views.pop(view_id, None)
            if view is not None:
                self._ended_bytes[view.camera] = self._ended_bytes.get(view.camera, 0) + view.bytes

    # -- sampling -------------------------------------------------------------

    def run(self, stopping: threading.Event) -> None:
        """Take a sample every INTERVAL seconds until ``stopping`` is set."""
        while True:
            try:
                self.sample()
            except Exception:
                log.exception("stats: sample failed")
            if stopping.wait(INTERVAL):
                return

    def sample(self) -> None:
        now = time.monotonic()
        elapsed = max(now - self._sampled, 1e-3)
        self._sampled = now
        status = self._service.describe()
        audience = self._service.audience()
        with self._lock:
            views = list(self._views.values())
            ended = dict(self._ended_bytes)

        cameras: list[dict[str, Any]] = []
        watchers: list[dict[str, Any]] = []
        previous, self._counters = self._counters, {}
        for st in status:
            name = st["name"]
            heard = audience.get(name, {"local": 0, "network": [], "network_bytes": 0})
            web = [view for view in views if view.camera == name]
            programs = heard["local"]  # the page's own subscribers aren't included
            counters = (
                st["cpu_seconds"],
                heard["network_bytes"],
                ended.get(name, 0) + sum(view.bytes for view in web),
            )
            self._counters[name] = counters
            cpu, net, web_sent = (
                _grew(new, old)
                for new, old in zip(counters, previous.get(name, counters), strict=True)
            )
            cameras.append(
                {
                    "name": name,
                    "state": st["state"],
                    "on_backup": st["on_backup"],
                    "source": st["source"],
                    "error": st["error"],
                    "fps": st["fps"],
                    "width": st["width"],
                    "height": st["height"],
                    "online_seconds": st["online_seconds"],
                    "backup_seconds": st["backup_seconds"],
                    "reconnects": st["reconnects"],
                    "last_frame_age": st["last_frame_age"],
                    "frames_shared": st["seq"],
                    "frames_capped": st["frames_capped"],
                    "frames_unwatched": st["frames_unwatched"],
                    "work_ms": st["work_ms"],
                    "cpu": round(cpu / elapsed * 100, 1),
                    "viewers": programs + len(heard["network"]) + len(web),
                    "sent_rate": round((net + web_sent) / elapsed),
                }
            )
            if programs:
                watchers.append({"camera": name, "kind": "programs", "count": programs})
            for address, since in heard["network"]:
                watchers.append(
                    {"camera": name, "kind": "machine", "address": address, "since": since}
                )
            for view in web:
                watchers.append(
                    {
                        "camera": name,
                        "kind": "panel" if view.panel else "link",
                        "address": view.address,
                        "app": view.app,
                        "since": view.since,
                        "bytes": view.bytes,
                    }
                )

        process = self._process.read()
        totals = {
            "viewers": sum(c["viewers"] for c in cameras),
            "sent_rate": sum(c["sent_rate"] for c in cameras),
            "online": sum(c["state"] == "streaming" for c in cameras),
        }
        wall = round(time.time(), 1)
        current = {
            "time": wall,
            "started": self._service.started,
            "process": process,
            "totals": totals,
            "cameras": cameras,
            "watchers": watchers,
        }
        point = {
            "t": wall,
            "cpu": process["cpu"],
            "memory": process["memory"],
            "sent": totals["sent_rate"],
            "viewers": totals["viewers"],
            "cameras": {
                c["name"]: {"fps": c["fps"], "viewers": c["viewers"], "sent": c["sent_rate"]}
                for c in cameras
            },
        }
        with self._lock:
            self._current = current
            self._samples.append(point)

    def report(self, since: float | None = None) -> dict[str, Any]:
        """The latest numbers, plus the samples taken after ``since`` (all of them if None)."""
        with self._lock:
            samples = [s for s in self._samples if since is None or s["t"] > since]
            current = self._current
        return {
            "ok": True,
            "interval": INTERVAL,
            "history": INTERVAL * HISTORY,
            "now": current,
            "samples": samples,
        }


def _grew(new: float, old: float) -> float:
    """How much a counter grew; a camera that restarted counts again from zero."""
    return new - old if new >= old else new


class _ProcessUsage:
    """This process's CPU (100% = one full core, like Activity Monitor and top),
    memory in use, and number of threads."""

    def __init__(self) -> None:
        self._cpu = time.process_time()
        self._at = time.monotonic()
        self._memory: int | None = None
        self._memory_at = float("-inf")

    def read(self) -> dict[str, Any]:
        cpu, at = time.process_time(), time.monotonic()
        percent = (cpu - self._cpu) / max(at - self._at, 1e-3) * 100
        self._cpu, self._at = cpu, at
        if sys.platform != "darwin" or at - self._memory_at >= _PS_EVERY:
            self._memory, self._memory_at = _memory_in_use(), at
        return {
            "cpu": round(percent, 1),
            "memory": self._memory,
            "threads": threading.active_count(),
            "cores": os.cpu_count(),
            "pid": os.getpid(),
        }


def _memory_in_use() -> int | None:
    """Resident memory in bytes (RSS), or None where it can't be read cheaply."""
    try:
        with open("/proc/self/statm") as statm:  # Linux
            return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(os.getpid())],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            ).stdout
            return int(out.strip()) * 1024
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    return None


_APPS = (
    ("VLC", "VLC"),
    ("Lavf", "OpenCV or FFmpeg"),  # OpenCV reads web links through FFmpeg
    ("GStreamer", "GStreamer"),
    ("OBS", "OBS"),
    ("python-requests", "Python"),
    ("Python-urllib", "Python"),
    ("aiohttp", "Python"),
    ("curl/", "curl"),
    ("Wget", "wget"),
)
_BROWSERS = (
    ("Edg", "Edge"),
    ("OPR/", "Opera"),
    ("CriOS", "Chrome"),
    ("FxiOS", "Firefox"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Macintosh", "Mac"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def app_name(user_agent: str) -> str:
    """A readable name for the app behind a User-Agent header, like "Safari on iPhone"."""
    for token, name in _APPS:
        if token in user_agent:
            return name
    browser = next((name for token, name in _BROWSERS if token in user_agent), None)
    system = next((name for token, name in _SYSTEMS if token in user_agent), None)
    if browser and system:
        return f"{browser} on {system}"
    if browser or system:
        return browser or f"An app on {system}"
    first = user_agent.split("/", 1)[0].strip()[:40]
    return first or "Unknown app"
