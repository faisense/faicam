"""Known IP-camera brands and how to find a camera's stream.

Most cameras serve RTSP on port 554 at a brand-specific path. Knowing the brand
turns "IP, username, password" into a working stream address; ``detect`` tries
the known paths when the brand is unknown.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import quote

import numpy as np
from faistream.streaming.sources import SourceError, make_source

from faicam.rtsp_check import Diagnosis, diagnose


@dataclass(frozen=True)
class Brand:
    label: str
    path: str  # main (highest quality) stream


BRANDS: dict[str, Brand] = {
    "hikvision": Brand("Hikvision", "/Streaming/Channels/101"),
    "dahua": Brand("Dahua / Amcrest / Lorex", "/cam/realmonitor?channel=1&subtype=0"),
    "hanwha": Brand("Hanwha / Wisenet", "/profile2/media.smp"),
    "axis": Brand("Axis", "/axis-media/media.amp"),
    "reolink": Brand("Reolink", "/h264Preview_01_main"),
    "uniview": Brand("Uniview", "/media/video1"),
    "tapo": Brand("TP-Link Tapo", "/stream1"),
    "foscam": Brand("Foscam", "/videoMain"),
}
BRAND_ALIASES = {"wisenet": "hanwha", "samsung": "hanwha", "amcrest": "dahua", "lorex": "dahua"}


def normalize_brand(brand: str) -> str:
    key = brand.strip().lower()
    return BRAND_ALIASES.get(key, key)


def rtsp_url(ip: str, path: str, username: str = "", password: str = "", port: int = 554) -> str:
    """Build a stream address; special characters in credentials are escaped."""
    auth = ""
    if username or password:
        auth = quote(username, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    host = f"[{ip}]" if ":" in ip else ip  # IPv6
    if not path.startswith("/"):
        path = "/" + path
    return f"rtsp://{auth}{host}:{port}{path}"


@dataclass(frozen=True)
class Detection:
    brand: str | None
    path: str
    image: np.ndarray = field(repr=False)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


class DetectError(RuntimeError):
    """Detection failed; the message says what to check."""


Probe = Callable[[str, float], np.ndarray]


def probe_stream(url: str, timeout: float) -> np.ndarray:
    """Open ``url`` and return one frame from it."""
    source = make_source(url, open_timeout=timeout, read_timeout=timeout)
    source.open()
    try:
        if not source.grab():
            raise SourceError("no frame received")
        return source.retrieve().copy()
    finally:
        source.close()


def reachable(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect(
    ip: str,
    username: str = "",
    password: str = "",
    *,
    brand: str | None = None,
    path: str | None = None,
    port: int = 554,
    timeout: float = 5.0,
    probe: Probe = probe_stream,
    check_reachable: Callable[[str, int], bool] = reachable,
    explain: Callable[[str, float], Diagnosis] = diagnose,
) -> Detection:
    """Find a working stream on a camera and read one frame from it.

    With ``path`` only that path is tried; with ``brand`` only that brand's path;
    otherwise every known brand's path, most common first.
    """
    if not check_reachable(ip, port):
        raise DetectError(
            f"Can't reach {ip} on port {port}. Check the IP address and that the camera is on "
            "the same network."
        )
    candidates: Iterable[tuple[str | None, str]]
    if path:
        candidates = [(normalize_brand(brand) if brand else None, path)]
    elif brand:
        key = normalize_brand(brand)
        if key not in BRANDS:
            raise DetectError(f"Unknown brand {brand!r}. Choose 'other' and enter the stream path.")
        candidates = [(key, BRANDS[key].path)]
    else:
        candidates = [(key, b.path) for key, b in BRANDS.items()]
    candidates = list(candidates)
    for found_brand, candidate in candidates:
        try:
            image = probe(rtsp_url(ip, candidate, username, password, port), timeout)
        except Exception:
            continue
        return Detection(found_brand, candidate, image)
    # Ask the camera itself why, using the first (or only) address tried.
    reason = explain(rtsp_url(ip, candidates[0][1], username, password, port), timeout)
    if reason.kind in ("auth", "udp_only", "ingest") or (len(candidates) == 1 and reason.message):
        raise DetectError(reason.message)
    raise DetectError(
        f"The camera at {ip} answered, but no video stream opened. Check the username and "
        "password, or choose the brand or enter the stream path from the camera's manual."
    )
