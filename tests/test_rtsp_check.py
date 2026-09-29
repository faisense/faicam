"""The RTSP checker against a small scripted RTSP server."""

import hashlib
import re
import socket
import socketserver
import threading
from collections.abc import Iterator

import numpy as np
import pytest

from faicam.brands import DetectError, detect
from faicam.rtsp_check import diagnose

SDP = (
    "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=test\r\nt=0 0\r\na=control:*\r\n"
    "m=audio 0 RTP/AVP 0\r\na=control:trackID=0\r\n"
    "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\na=control:trackID=1\r\n"
)
REALM, NONCE = "cam", "abc123"


def digest_ok(header: str, method: str, username: str, password: str) -> bool:
    fields = dict(re.findall(r'(\w+)="?([^",]*)"?', header))
    ha1 = hashlib.md5(f"{username}:{REALM}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{fields.get('uri')}".encode()).hexdigest()
    return fields.get("response") == hashlib.md5(f"{ha1}:{NONCE}:{ha2}".encode()).hexdigest()


class FakeCamera(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, behaviour: str):
        self.behaviour = behaviour
        self.setup_urls: list[str] = []
        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class Handler(socketserver.StreamRequestHandler):
    server: FakeCamera

    def handle(self) -> None:
        while True:
            lines = []
            while (line := self.rfile.readline().decode()) not in ("\r\n", ""):
                lines.append(line.strip())
            if not lines:
                return
            method, url, _ = lines[0].split(" ")
            headers = {k.lower(): v.strip() for k, v in (h.split(":", 1) for h in lines[1:])}
            status, extra, body = self.answer(method, url, headers)
            head = [f"RTSP/1.0 {status} X", f"CSeq: {headers.get('cseq', '0')}", *extra]
            if body:
                head += ["Content-Type: application/sdp", f"Content-Length: {len(body)}"]
            self.wfile.write(("\r\n".join(head) + "\r\n\r\n" + body).encode())

    def answer(self, method: str, url: str, headers: dict[str, str]) -> tuple[int, list[str], str]:
        behaviour = self.server.behaviour
        if behaviour == "auth" and not digest_ok(
            headers.get("authorization", ""), method, "admin", "right"
        ):
            return 401, [f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}"'], ""
        if method == "DESCRIBE":
            if behaviour == "not_found":
                return 404, [], ""
            return 200, ["Session: 42;timeout=60", f"Content-Base: {url}/"], SDP
        if method == "SETUP":
            self.server.setup_urls.append(url)
            tcp = "TCP" in headers.get("transport", "")
            if behaviour == "ingest":
                return 403, [], ""
            if behaviour == "udp_only" and tcp:
                return 461, [], ""
            return 200, ["Session: 42"], ""
        return 400, [], ""


@pytest.fixture
def camera(request: pytest.FixtureRequest) -> Iterator[FakeCamera]:
    server = FakeCamera(request.param)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def url(camera: FakeCamera, login: str = "") -> str:
    return f"rtsp://{login}127.0.0.1:{camera.port}/live/main"


@pytest.mark.parametrize("camera", ["ok"], indirect=True)
def test_working_stream(camera: FakeCamera) -> None:
    assert diagnose(url(camera)).kind == "ok"
    # SETUP goes to the video track, resolved against Content-Base.
    assert camera.setup_urls == [f"rtsp://127.0.0.1:{camera.port}/live/main/trackID=1"]


@pytest.mark.parametrize("camera", ["ingest"], indirect=True)
def test_ingest_address(camera: FakeCamera) -> None:
    result = diagnose(url(camera))
    assert result.kind == "ingest" and "playback address" in result.message


@pytest.mark.parametrize("camera", ["not_found"], indirect=True)
def test_no_stream_at_path(camera: FakeCamera) -> None:
    assert diagnose(url(camera)).kind == "not_found"


@pytest.mark.parametrize("camera", ["udp_only"], indirect=True)
def test_udp_only_server(camera: FakeCamera) -> None:
    result = diagnose(url(camera))
    assert result.kind == "udp_only" and "UDP" in result.message


@pytest.mark.parametrize("camera", ["auth"], indirect=True)
def test_digest_authentication(camera: FakeCamera) -> None:
    assert diagnose(url(camera)).message == "This stream needs a username and password."
    assert (
        diagnose(url(camera, "admin:wrong@")).message
        == "The username or password was not accepted."
    )
    assert diagnose(url(camera, "admin:right@")).kind == "ok"


def test_unreachable() -> None:
    assert diagnose("rtsp://127.0.0.1:1/x").kind == "unreachable"


def test_not_an_rtsp_server() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]

        def reply() -> None:
            conn, _ = server.accept()
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            conn.close()

        threading.Thread(target=reply, daemon=True).start()
        assert diagnose(f"rtsp://127.0.0.1:{port}/x").kind == "not_rtsp"


@pytest.mark.parametrize("camera", ["auth"], indirect=True)
def test_camera_detection_reports_a_wrong_password(camera: FakeCamera) -> None:
    def probe(url: str, timeout: float) -> np.ndarray:
        raise RuntimeError("OpenCV only says it failed")

    with pytest.raises(DetectError, match="username or password was not accepted"):
        detect("127.0.0.1", "admin", "wrong", port=camera.port, probe=probe)
