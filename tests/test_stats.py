import sys
import time
from typing import Any, cast

import pytest
from faistream.streaming.service import Service

from faicam.stats import Stats, WebView, _ProcessUsage, app_name


class FakeService:
    """Just what the stats need: each camera's status and who is connected."""

    started = 1_000_000.0

    def __init__(self) -> None:
        self.status: dict[str, Any] = {
            "name": "cam",
            "state": "streaming",
            "on_backup": False,
            "source": "synthetic://64x48@30",
            "error": None,
            "fps": 30.0,
            "width": 64,
            "height": 48,
            "online_seconds": 5.0,
            "backup_seconds": 0.0,
            "reconnects": 0,
            "last_frame_age": 0.0,
            "seq": 150,
            "frames_capped": 0,
            "frames_unwatched": 0,
            "work_ms": 1.5,
            "cpu_seconds": 0.0,
        }
        self.heard: dict[str, Any] = {"local": 0, "network": [], "network_bytes": 0}

    def describe(self) -> list[dict[str, Any]]:
        return [dict(self.status)]

    def audience(self) -> dict[str, dict[str, Any]]:
        return {"cam": dict(self.heard)}


def make() -> tuple[FakeService, Stats]:
    service = FakeService()
    return service, Stats(cast(Service, service))


def test_counts_everyone_watching() -> None:
    service, stats = make()
    service.heard = {"local": 2, "network": [("192.168.1.9", 99.0)], "network_bytes": 0}
    stats.open_view(WebView("cam", "192.168.1.30", "Safari on iPhone", panel=False, since=50.0))
    stats.sample()
    now = stats.report()["now"]
    assert now["cameras"][0]["viewers"] == 4  # 2 programs + 1 machine + 1 web link
    assert now["totals"]["viewers"] == 4
    assert now["watchers"] == [
        {"camera": "cam", "kind": "programs", "count": 2},
        {"camera": "cam", "kind": "machine", "address": "192.168.1.9", "since": 99.0},
        {
            "camera": "cam",
            "kind": "link",
            "address": "192.168.1.30",
            "app": "Safari on iPhone",
            "since": 50.0,
            "bytes": 0,
        },
    ]
    assert now["started"] == FakeService.started


def test_rates() -> None:
    service, stats = make()
    view = WebView("cam", "10.0.0.2", "VLC", panel=False)
    stats.open_view(view)
    stats.sample()
    first = stats.report()["now"]["cameras"][0]
    assert first["sent_rate"] == 0 and first["cpu"] == 0  # nothing to compare with yet

    service.status["cpu_seconds"] = 0.05
    service.heard["network_bytes"] = 40_000
    view.bytes = 10_000
    time.sleep(0.1)
    stats.sample()
    cam = stats.report()["now"]["cameras"][0]
    assert 50_000 < cam["sent_rate"] <= 50_000 / 0.1  # 50 KB in a little over 0.1 s
    assert 0 < cam["cpu"] <= 50

    # a camera restart counts from zero again; a closed web view keeps its bytes
    service.status["cpu_seconds"] = 0.0
    service.heard["network_bytes"] = 0
    stats.close_view(1)
    stats.sample()
    cam = stats.report()["now"]["cameras"][0]
    assert cam["sent_rate"] == 0 and cam["cpu"] == 0


def test_history_since() -> None:
    _, stats = make()
    for _ in range(3):
        stats.sample()
        time.sleep(0.11)  # sample times are kept to 0.1 s
    report = stats.report()
    assert [s["cameras"]["cam"]["fps"] for s in report["samples"]] == [30.0] * 3
    after = report["samples"][0]["t"]
    assert len(stats.report(since=after)["samples"]) == 2
    assert report["interval"] == 2 and report["history"] == 3600


def test_process_usage() -> None:
    usage = _ProcessUsage()
    sum(range(200_000))  # use a little CPU
    reading = usage.read()
    assert reading["cpu"] >= 0 and reading["threads"] >= 1 and reading["cores"] >= 1
    if sys.platform in ("linux", "darwin"):
        assert reading["memory"] > 1_000_000


@pytest.mark.parametrize(
    ("user_agent", "name"),
    [
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Safari on iPhone",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Chrome on Mac",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
            "Edge on Windows",
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
            "Firefox on Linux",
        ),
        ("VLC/3.0.21 LibVLC/3.0.21", "VLC"),
        ("Lavf/61.1.100", "OpenCV or FFmpeg"),
        ("curl/8.7.1", "curl"),
        ("Python-urllib/3.12", "Python"),
        ("HomeCam/2.1 (build 7)", "HomeCam"),
        ("", "Unknown app"),
    ],
)
def test_app_names(user_agent: str, name: str) -> None:
    assert app_name(user_agent) == name
