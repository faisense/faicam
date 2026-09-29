"""Explain why an RTSP address doesn't play.

When OpenCV can't open a stream it only reports failure. This module speaks just
enough RTSP (DESCRIBE, then SETUP of the first track, with Basic or Digest
authentication) to tell the user what the server actually said.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


@dataclass(frozen=True)
class Diagnosis:
    kind: str  # ok, unreachable, auth, not_found, forbidden, ingest, udp_only, not_rtsp, other
    message: str


_INGEST = (
    "The server lists this stream but won't let anyone play it. This usually means it's an "
    "ingest address, where a camera or encoder sends video to a streaming service such as "
    "Wowza. Use the service's playback address instead (often an https://…/playlist.m3u8 "
    "link), or the camera's own RTSP address."
)


def diagnose(url: str, timeout: float = 5.0) -> Diagnosis:
    parts = urlsplit(url)
    if parts.scheme != "rtsp" or not parts.hostname:
        return Diagnosis("other", "")
    host, port = parts.hostname, parts.port or 554
    username, password = unquote(parts.username or ""), unquote(parts.password or "")
    netloc = f"[{host}]" if ":" in host else host
    clean = urlunsplit(parts._replace(netloc=f"{netloc}:{port}"))
    try:
        conn = socket.create_connection((host, port), timeout)
    except OSError:
        return Diagnosis(
            "unreachable", f"Can't reach {host} on port {port}. Check the address and the network."
        )
    try:
        with conn:
            session = _Session(conn, username, password, timeout)
            status, headers, body = session.request(
                "DESCRIBE", clean, {"Accept": "application/sdp"}
            )
            if status in (401, 407):
                return _auth(username)
            if status == 404:
                return Diagnosis(
                    "not_found",
                    "The server has no stream at this address. Check the path after the port.",
                )
            if status == 403:
                return Diagnosis("forbidden", "The server refused access to this stream.")
            if status != 200:
                return Diagnosis(
                    "other", f"The server answered the stream request with error {status}."
                )
            track = _first_track(clean, headers, body)
            extra = {"Session": headers["session"].split(";")[0]} if "session" in headers else {}
            status, _, _ = session.request(
                "SETUP", track, {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1", **extra}
            )
            if status == 461:  # TCP not supported: does UDP work?
                udp = "RTP/AVP;unicast;client_port=50000-50001"
                status, _, _ = session.request("SETUP", track, {"Transport": udp, **extra})
                if status == 200:
                    return Diagnosis(
                        "udp_only", "This server only streams over UDP. Set 'Connect over' to UDP."
                    )
            if status in (401, 407):
                return _auth(username)
            if status in (403, 405, 455):
                return Diagnosis("ingest", _INGEST)
            if status != 200:
                return Diagnosis(
                    "other", f"The server refused to start the stream (error {status})."
                )
            return Diagnosis("ok", "The server accepts the stream.")
    except (OSError, ValueError):
        return Diagnosis("not_rtsp", f"{host} on port {port} didn't answer like an RTSP server.")


def _auth(username: str) -> Diagnosis:
    if username:
        return Diagnosis("auth", "The username or password was not accepted.")
    return Diagnosis("auth", "This stream needs a username and password.")


def _first_track(url: str, headers: dict[str, str], sdp: str) -> str:
    """The SETUP address of the first video track (or the first track) in the SDP."""
    base = headers.get("content-base") or headers.get("content-location") or url
    control, current, video = None, None, False
    for line in sdp.splitlines():
        if line.startswith("m="):
            if control is not None:
                break
            video = line.startswith("m=video")
        elif line.startswith("a=control:") and (video or current is None):
            current = line.split(":", 1)[1].strip()
            if video:
                control = current
    control = control or current or "*"
    if control == "*":
        return base
    if control.startswith("rtsp://"):
        return control
    return urljoin(base if base.endswith("/") else base + "/", control)


class _Session:
    def __init__(self, conn: socket.socket, username: str, password: str, timeout: float):
        self.conn = conn
        self.username, self.password = username, password
        self.cseq = 0
        self.challenge: dict[str, str] | None = None
        self.basic = False
        conn.settimeout(timeout)

    def request(
        self, method: str, url: str, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], str]:
        status, head, body = self._send(method, url, headers)
        if status == 401 and self.username and self.challenge is None and not self.basic:
            self._remember_challenge(head)
            status, head, body = self._send(method, url, headers)
        return status, head, body

    def _send(
        self, method: str, url: str, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], str]:
        self.cseq += 1
        lines = [f"{method} {url} RTSP/1.0", f"CSeq: {self.cseq}", "User-Agent: faicam"]
        lines += [f"{k}: {v}" for k, v in headers.items()]
        if auth := self._authorization(method, url):
            lines.append(f"Authorization: {auth}")
        self.conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return self._read()

    def _read(self) -> tuple[int, dict[str, str], str]:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.conn.recv(4096)
            if not chunk:
                raise ValueError("connection closed")
            data += chunk
            if len(data) > 65536:
                raise ValueError("response too large")
        raw_head, _, body = data.partition(b"\r\n\r\n")
        status_line, *header_lines = raw_head.decode("latin-1").split("\r\n")
        match = re.match(r"RTSP/\d\.\d (\d{3})", status_line)
        if not match:
            raise ValueError("not an RTSP response")
        head: dict[str, str] = {}
        for line in header_lines:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            head[key] = f"{head[key]}, {value.strip()}" if key in head else value.strip()
        length = int(head.get("content-length", "0") or 0)
        while len(body) < length:
            chunk = self.conn.recv(4096)
            if not chunk:
                break
            body += chunk
        return int(match[1]), head, body[:length].decode("utf-8", "replace")

    def _remember_challenge(self, head: dict[str, str]) -> None:
        header = head.get("www-authenticate", "")
        if "digest" in header.lower():
            digest = header[header.lower().index("digest") + 6 :]
            self.challenge = dict(re.findall(r'(\w+)="?([^",]*)"?', digest))
        else:
            self.basic = True

    def _authorization(self, method: str, url: str) -> str | None:
        if self.basic:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return f"Basic {token}"
        if self.challenge is None:
            return None
        realm, nonce = self.challenge.get("realm", ""), self.challenge.get("nonce", "")

        def md5(text: str) -> str:
            # MD5 is what RTSP Digest authentication specifies; it isn't used for security here.
            return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()

        ha1 = md5(f"{self.username}:{realm}:{self.password}")
        ha2 = md5(f"{method}:{url}")
        fields = f'username="{self.username}", realm="{realm}", nonce="{nonce}", uri="{url}"'
        if "auth" in self.challenge.get("qop", "").split(","):
            cnonce, nc = os.urandom(8).hex(), f"{self.cseq:08x}"
            response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
            return f'Digest {fields}, qop=auth, nc={nc}, cnonce="{cnonce}", response="{response}"'
        return f'Digest {fields}, response="{md5(f"{ha1}:{nonce}:{ha2}")}"'
