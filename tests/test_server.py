import base64
import json
import os
import stat
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from faistream import Service
from faistream.streaming.sources import split_credentials

from faicam.config import ConfigFile, UIConfig
from faicam.server import WebUI
from tests.helpers import free_port_block, stream_port

pytest.importorskip("cv2")  # the page's pictures are JPEG


class Client:
    def __init__(self, port: int, password: str | None = None):
        self.base = f"http://127.0.0.1:{port}"
        self.password = password

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any, dict[str, str]]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if self.password:
            token = base64.b64encode(f"admin:{self.password}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, raw, head = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            status, raw, head = error.code, error.read(), dict(error.headers)
        if head.get("Content-Type", "").startswith("application/json"):
            return status, json.loads(raw), head
        return status, raw, head

    def state(self) -> dict[str, Any]:
        status, body, _ = self.call("GET", "/api/state")
        assert status == 200
        return body  # type: ignore[no-any-return]

    def wait_streaming(self, name: str) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            cams = {c["name"]: c for c in self.state()["cameras"]}
            if cams.get(name, {}).get("state") == "streaming":
                return cams[name]  # type: ignore[no-any-return]
            time.sleep(0.1)
        raise AssertionError(f"{name} never started streaming")


def start(tmp_path: Path, ui: UIConfig | None = None, editable: bool = True):  # type: ignore[no-untyped-def]
    port = free_port_block()
    file = ConfigFile.open(tmp_path / "cameras.yaml")
    file.overrides = {"port": port}
    service = Service(file.config())
    service.start()
    page = WebUI(service, ui or UIConfig(port=free_port_block(1)), file if editable else None)
    page.start()
    return service, page, file


@pytest.fixture
def app(tmp_path: Path) -> Iterator[tuple[Client, ConfigFile]]:
    service, page, file = start(tmp_path)
    try:
        yield Client(page.ui.port), file
    finally:
        page.stop()
        service.stop()


SYNTHETIC = {"kind": "source", "source": "synthetic://64x48@30"}


def synthetic() -> dict[str, object]:
    """A new synthetic camera's form fields, with a port of its own."""
    return {**SYNTHETIC, "stream_port": stream_port()}


def test_page_and_empty_state(app: tuple[Client, ConfigFile]) -> None:
    client, _ = app
    status, html, head = client.call("GET", "/")
    assert status == 200 and b'<div id="app">' in html
    assert head["X-Frame-Options"] == "DENY"
    assert "script-src" not in head["Content-Security-Policy"]  # 'self' only, no inline code
    assert "default-src 'self'" in head["Content-Security-Policy"]
    state = client.state()
    assert state["cameras"] == [] and state["editable"] is True
    assert {"key": "hikvision", "label": "Hikvision"} in state["brands"]


def test_add_edit_and_remove(app: tuple[Client, ConfigFile]) -> None:
    client, file = app
    added = {"name": "cam", **synthetic()}
    assert client.call("POST", "/api/cameras", added)[0] == 200
    cam = client.wait_streaming("cam")
    assert (cam["width"], cam["height"]) == (64, 48)
    assert file.path.exists() and stat.S_IMODE(os.stat(file.path).st_mode) == 0o600

    status, jpeg, head = client.call("GET", "/snapshot/cam.jpg?width=320")
    assert status == 200 and head["Content-Type"] == "image/jpeg" and jpeg[:2] == b"\xff\xd8"
    assert client.state()["cameras"][0]["watching"] == 0  # the page's own viewer doesn't count

    body = {"name": "lobby", "original": "cam", **SYNTHETIC, "fps": "10"}
    assert client.call("POST", "/api/cameras", body)[0] == 200
    client.wait_streaming("lobby")
    assert [c["name"] for c in client.state()["cameras"]] == ["lobby"]
    assert ConfigFile.open(file.path).cameras() == {  # the edit didn't send a port: kept
        "lobby": {"source": "synthetic://64x48@30", "stream_port": added["stream_port"], "fps": 10}
    }

    assert client.call("DELETE", "/api/cameras/lobby")[0] == 200
    assert client.state()["cameras"] == []
    assert ConfigFile.open(file.path).cameras() == {}


def test_backup_by_name(app: tuple[Client, ConfigFile]) -> None:
    client, file = app
    client.call("POST", "/api/cameras", {"name": "a", **synthetic()})
    backups = [{"type": "camera", "value": "a"}]
    client.call("POST", "/api/cameras", {"name": "b", **synthetic(), "backups": backups})
    assert ConfigFile.open(file.path).cameras()["b"]["backup"] == "a"
    assert client.state()["cameras"][1]["settings"]["backups"] == backups


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({"name": "bad name", **synthetic()}, 400, "lowercase letters, digits"),
        ({"name": "cam", "stream_port": stream_port(), "kind": "camera"}, 400, "IP address"),
        (
            {
                "name": "cam",
                "stream_port": stream_port(),
                "kind": "camera",
                "ip": "10.0.0.5",
                "brand": "other",
            },
            400,
            "path",
        ),
        ({"name": "cam", "stream_port": stream_port(), "kind": "source"}, 400, "Enter the source"),
        ({"name": "cam", **synthetic(), "fps": "fast"}, 400, "must be a number"),
    ],
)
def test_form_errors_are_readable(
    app: tuple[Client, ConfigFile], body: dict[str, Any], status: int, message: str
) -> None:
    client, _ = app
    code, reply, _ = client.call("POST", "/api/cameras", body)
    assert code == status and message in reply["error"]


def test_duplicate_name(app: tuple[Client, ConfigFile]) -> None:
    client, _ = app
    client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    code, reply, _ = client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    assert code == 400 and "already exists" in reply["error"]


def test_unreachable_camera_is_explained_and_not_saved(app: tuple[Client, ConfigFile]) -> None:
    client, file = app
    camera = {
        "name": "cam",
        "stream_port": stream_port(),
        "kind": "camera",
        "ip": "127.0.0.1",
        "port": 1,
        "brand": "auto",
    }
    code, reply, _ = client.call("POST", "/api/detect", camera)
    assert code == 422 and "Can't reach 127.0.0.1 on port 1" in reply["error"]
    code, reply, _ = client.call("POST", "/api/cameras", camera)
    assert code == 422 and not file.path.exists()


def test_passwords_never_reach_the_page(app: tuple[Client, ConfigFile]) -> None:
    client, file = app
    camera = {
        "name": "cam",
        "stream_port": stream_port(),
        "kind": "camera",
        "ip": "127.0.0.1",
        "port": 1,
        "brand": "hikvision",  # a known brand is saved without probing the camera
        "username": "admin",
        "password": "s3cret!",
    }
    assert client.call("POST", "/api/cameras", camera)[0] == 200
    client.call(
        "POST",
        "/api/cameras",
        {
            "name": "src",
            "stream_port": stream_port(),
            "kind": "source",
            "source": "rtsp://u:s3cret!@127.0.0.1:1/x",
        },
    )
    state = client.state()
    assert "s3cret" not in json.dumps(state)
    assert state["cameras"][0]["settings"]["has_password"] is True

    # Saving the form again without a password keeps the stored one.
    assert (
        client.call("POST", "/api/cameras", {**camera, "original": "cam", "password": ""})[0] == 200
    )
    assert ConfigFile.open(file.path).cameras()["cam"]["password"] == "s3cret!"
    # So does saving a stream address shown without its password, on either form tab.
    shown = next(c for c in state["cameras"] if c["name"] == "src")["settings"]
    assert shown["kind"] == "rtsp" and shown["source"] == "rtsp://127.0.0.1:1/x"
    assert shown["username"] == "u" and shown["has_password"] is True
    for kind in ("rtsp", "source"):
        body = {"name": "src", "original": "src", "kind": kind, "source": shown["source"]}
        assert client.call("POST", "/api/cameras", {**body, "username": "u"})[0] == 200
        saved = ConfigFile.open(file.path).cameras()["src"]["source"]
        assert split_credentials(saved) == ("rtsp://127.0.0.1:1/x", "u", "s3cret!"), kind


def test_live_video(app: tuple[Client, ConfigFile]) -> None:
    client, _ = app
    client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    client.wait_streaming("cam")
    with urllib.request.urlopen(client.base + "/live/cam.mjpg", timeout=10) as response:
        assert response.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        chunk = response.read(4096)
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg") and b"\xff\xd8" in chunk


def test_rejects_other_sites(app: tuple[Client, ConfigFile]) -> None:
    client, _ = app
    port = client.base.rsplit(":", 1)[1]
    assert client.call("GET", "/api/state", headers={"Host": f"evil.example:{port}"})[0] == 403
    body = {"name": "cam", **synthetic()}
    assert client.call("POST", "/api/cameras", body, {"Origin": "http://evil.example"})[0] == 403
    request = urllib.request.Request(client.base + "/api/cameras", data=b"{}", method="POST")
    request.add_header("Content-Type", "text/plain")
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(request, timeout=5)
    assert info.value.code == 415


def test_password_protected_page(tmp_path: Path) -> None:
    ui = UIConfig(port=free_port_block(1), page="network", password="pw", bind="127.0.0.1")
    service, page, _ = start(tmp_path, ui)
    try:
        code, _, head = Client(page.ui.port).call("GET", "/api/state")
        assert code == 401 and head["WWW-Authenticate"].startswith("Basic")
        assert Client(page.ui.port, password="wrong").call("GET", "/api/state")[0] == 401
        assert Client(page.ui.port, password="pw").call("GET", "/api/state")[0] == 200
    finally:
        page.stop()
        service.stop()


def test_read_only_without_config_file(tmp_path: Path) -> None:
    service, page, _ = start(tmp_path, editable=False)
    try:
        client = Client(page.ui.port)
        assert client.state()["editable"] is False
        code, reply, _ = client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
        assert code == 409 and "config file" in reply["error"]
    finally:
        page.stop()
        service.stop()


def test_read_only_config_file_is_explained(app: tuple[Client, ConfigFile], tmp_path: Path) -> None:
    client, file = app
    client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    os.chmod(file.path, 0o400)
    os.chmod(tmp_path, 0o500)  # can't write the file or replace it
    try:
        code, reply, _ = client.call("POST", "/api/cameras", {"name": "two", **synthetic()})
    finally:
        os.chmod(tmp_path, 0o700)
    assert code == 500 and "Could not save" in reply["error"]
    assert [c["name"] for c in client.state()["cameras"]] == ["cam"]  # nothing half-applied


def test_missing_file_is_reported_before_saving(app: tuple[Client, ConfigFile]) -> None:
    client, file = app
    body = {
        "name": "public",
        "stream_port": stream_port(),
        "kind": "source",
        "source": "~/Movies/no-such-video.mp4",
    }
    code, reply, _ = client.call("POST", "/api/cameras", body)
    assert code == 400 and "There's no file at" in reply["error"]
    assert "~" not in reply["error"]  # shows the full path it looked in
    assert not file.path.exists()


def test_test_button_works_for_other_sources(app: tuple[Client, ConfigFile]) -> None:
    client, _ = app
    code, reply, _ = client.call("POST", "/api/detect", SYNTHETIC)
    assert code == 200 and (reply["width"], reply["height"]) == (64, 48)
    assert reply["preview"].startswith("data:image/jpeg;base64,")
    code, reply, _ = client.call("POST", "/api/detect", {"kind": "source", "source": "~/nope.mp4"})
    assert code == 400 and "There's no file at" in reply["error"]


def test_rtsp_address_with_separate_login(app: tuple[Client, ConfigFile]) -> None:
    client, file = app
    body = {
        "name": "cloud",
        "stream_port": stream_port(),
        "kind": "rtsp",
        "source": "rtsp://127.0.0.1:1/app/stream",
        "username": "me@example.com",
        "password": "p@ss:w/rd",
    }
    assert client.call("POST", "/api/cameras", body)[0] == 200
    saved = ConfigFile.open(file.path).cameras()["cloud"]["source"]
    assert saved == "rtsp://me%40example.com:p%40ss%3Aw%2Frd@127.0.0.1:1/app/stream"
    settings = client.state()["cameras"][0]["settings"]
    assert settings["username"] == "me@example.com" and "p@ss" not in json.dumps(settings)


@pytest.mark.parametrize(
    ("source", "message"),
    [("", "Enter the stream address"), ("/videos/a.mp4", "should start with rtsp://")],
)
def test_rtsp_address_errors(app: tuple[Client, ConfigFile], source: str, message: str) -> None:
    client, _ = app
    code, reply, _ = client.call(
        "POST",
        "/api/cameras",
        {"name": "x", "stream_port": stream_port(), "kind": "rtsp", "source": source},
    )
    assert code == 400 and message in reply["error"]


def test_rtsp_test_explains_failures(app: tuple[Client, ConfigFile]) -> None:
    client, _ = app
    body = {"kind": "rtsp", "source": "rtsp://127.0.0.1:1/live"}
    code, reply, _ = client.call("POST", "/api/detect", body)
    assert code == 422 and "Can't reach 127.0.0.1 on port 1" in reply["error"]


def test_live_link_works_in_opencv(app: tuple[Client, ConfigFile]) -> None:
    """The camera's web address is a normal video stream other programs can open."""
    import cv2

    client, _ = app
    body = {
        "name": "cam-1",
        "stream_port": stream_port(),
        "kind": "source",
        "source": "synthetic://128x96@30",
    }
    client.call("POST", "/api/cameras", body)
    client.wait_streaming("cam-1")
    capture = cv2.VideoCapture(client.base + "/live/cam-1.mjpg?fps=30&width=64")
    try:
        ok, image = capture.read()
    finally:
        capture.release()
    assert ok and image.shape == (48, 64, 3)


@pytest.mark.parametrize(
    "path",
    [
        "/live/cam.mjpg?fps=0",
        "/live/cam.mjpg?fps=fast",
        "/live/cam.mjpg?width=32",
        "/snapshot/cam.jpg?quality=200",
    ],
)
def test_link_options_are_checked(app: tuple[Client, ConfigFile], path: str) -> None:
    client, _ = app
    code, reply, _ = client.call("GET", path)
    assert code == 400 and "must be a number" in reply["error"]


def test_live_viewers_share_one_encode(
    app: tuple[Client, ConfigFile], monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    client, _ = app
    client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    client.wait_streaming("cam")
    encodes: list[int] = []
    original = WebUI._jpeg

    def counting(image: Any, width: int, quality: int) -> bytes:
        encodes.append(1)
        return original(image, width, quality)

    monkeypatch.setattr(WebUI, "_jpeg", staticmethod(counting))
    frames: list[int] = []

    def watch() -> None:
        with urllib.request.urlopen(client.base + "/live/cam.mjpg?fps=30", timeout=10) as response:
            data = b""
            while data.count(b"--frame") < 20:
                data += response.read(1024)
        frames.append(data.count(b"--frame"))

    viewers = [threading.Thread(target=watch) for _ in range(3)]
    for viewer in viewers:
        viewer.start()
    for viewer in viewers:
        viewer.join(timeout=15)
    assert sum(frames) >= 60
    assert len(encodes) < sum(frames) * 0.6  # far fewer encodes than frames sent


def test_live_link_with_password_in_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools on other devices take the camera-links password inside the link."""
    import cv2

    from faicam import access

    ui = UIConfig(
        port=free_port_block(1), bind="127.0.0.1", links="network", links_password="s3cret"
    )
    service, page, _ = start(tmp_path, ui)
    try:
        client = Client(page.ui.port)
        client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
        client.wait_streaming("cam")
        monkeypatch.setattr(access, "is_loopback", lambda address: False)  # as if from elsewhere
        base = f"http://127.0.0.1:{page.ui.port}/live/cam.mjpg"
        for url, works in ((base, False), (base.replace("//", "//viewer:s3cret@"), True)):
            capture = cv2.VideoCapture(url)
            try:
                assert capture.isOpened() == works and (capture.read()[0] if works else True)
            finally:
                capture.release()
    finally:
        page.stop()
        service.stop()


def test_browsers_get_a_viewer_page(app: tuple[Client, ConfigFile]) -> None:
    """Browsers opening the link get a page (Safari never finishes a bare MJPEG); apps get video."""
    client, _ = app
    client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    client.wait_streaming("cam")
    browser = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    code, html, head = client.call("GET", "/live/cam.mjpg?fps=30", headers=browser)
    assert code == 200 and head["Content-Type"].startswith("text/html")
    assert b'id="video"' in html  # the page starts the video itself, once it has loaded
    assert b"<button" not in html  # video only: pausing is in the control panel
    code, status, _ = client.call("GET", "/live/cam.json")  # what the page checks
    assert code == 200 and status["state"] == "streaming"
    for headers, path in (
        ({"Accept": "*/*"}, "/live/cam.mjpg"),
        (browser, "/live/cam.mjpg?mjpeg=1"),
    ):
        request = urllib.request.Request(client.base + path, headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.headers["Content-Type"].startswith("multipart/x-mixed-replace")
    code, _, _ = client.call("GET", "/live/nope.mjpg", headers=browser)
    assert code == 404


@pytest.mark.parametrize(
    ("ui", "shared", "page_shared"),
    [
        ({"links": "network"}, True, False),
        ({"page": "network", "password": "pw"}, False, True),
        ({"host": "0.0.0.0", "password": "pw"}, False, True),  # older files
    ],
)
def test_links_use_the_network_address_when_shared(
    tmp_path: Path, ui: dict[str, Any], shared: bool, page_shared: bool
) -> None:
    config = UIConfig.model_validate({"port": free_port_block(1), "bind": "127.0.0.1", **ui})
    service, page, _ = start(tmp_path, config)
    try:
        links = Client(page.ui.port, password=ui.get("password")).state()["links"]
        assert (links["shared"], links["page_shared"]) == (shared, page_shared)
    finally:
        page.stop()
        service.stop()


def test_paused_viewers_end_their_stream(app: tuple[Client, ConfigFile]) -> None:
    """Pause asks the server to end the stream; Safari would otherwise keep it open."""
    import threading

    client, _ = app
    client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    client.wait_streaming("cam")
    ended = threading.Event()

    def watch() -> None:
        with urllib.request.urlopen(
            client.base + "/live/cam.mjpg?id=viewer123", timeout=10
        ) as response:
            while response.read(4096):
                pass
        ended.set()  # the server closed the connection

    threading.Thread(target=watch, daemon=True).start()
    time.sleep(0.5)
    assert not ended.is_set()
    assert client.call("POST", "/api/stop-stream?id=viewer123", {})[0] == 200
    assert ended.wait(5)
    assert client.call("POST", "/api/stop-stream?id=x", {})[0] == 400  # ids are 8+ characters


def test_pause_camera_from_the_control_panel(app: tuple[Client, ConfigFile]) -> None:
    """Pause stops the camera's streaming for everyone until it's resumed."""
    import threading

    client, _ = app
    client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
    client.wait_streaming("cam")
    ended = threading.Event()

    def watch() -> None:  # someone watching the link when it's paused
        with urllib.request.urlopen(client.base + "/live/cam.mjpg", timeout=10) as response:
            while response.read(4096):
                pass
        ended.set()

    threading.Thread(target=watch, daemon=True).start()
    time.sleep(0.5)
    assert client.call("POST", "/api/cameras/cam/pause", {})[0] == 200
    assert ended.wait(5)  # open streams end
    assert client.state()["cameras"][0]["state"] == "paused"
    code, reply, _ = client.call("GET", "/snapshot/cam.jpg")
    assert code == 503 and "cam is paused" in reply["error"]
    assert client.call("GET", "/live/cam.mjpg", headers={"Accept": "*/*"})[0] == 503
    code, html, _ = client.call("GET", "/live/cam.mjpg", headers={"Accept": "text/html"})
    assert code == 200 and b'id="video"' in html  # the viewer page says it's paused, then plays
    assert client.call("GET", "/live/cam.json")[1]["state"] == "paused"
    assert client.call("POST", "/api/cameras/cam/resume", {})[0] == 200
    client.wait_streaming("cam")
    assert client.call("GET", "/snapshot/cam.jpg")[0] == 200


def test_stats_page(tmp_path: Path) -> None:
    service, page, _ = start(tmp_path)
    client = Client(page.ui.port)
    try:
        status, html, _ = client.call("GET", "/stats")
        assert status == 200 and b"/assets/stats-" in html

        client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
        client.wait_streaming("cam")
        vlc = urllib.request.Request(
            client.base + "/live/cam.mjpg", headers={"User-Agent": "VLC/3.0.21 LibVLC/3.0.21"}
        )
        chrome = urllib.request.Request(
            client.base + "/live/cam.mjpg?id=panel-view-1",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/128.0 Safari/537.36"},
        )
        with (
            urllib.request.urlopen(vlc, timeout=10) as a,
            urllib.request.urlopen(chrome, timeout=10) as b,
        ):
            a.read(2048)
            b.read(2048)
            page.stats.sample()
            status, report, _ = client.call("GET", "/api/stats")
        assert status == 200
        now = report["now"]
        watchers = sorted(now["watchers"], key=lambda w: w["kind"])
        assert [(w["kind"], w["app"], w["address"]) for w in watchers] == [
            ("link", "VLC", "127.0.0.1"),
            ("panel", "Chrome on Mac", "127.0.0.1"),
        ]
        assert all(w["bytes"] > 0 for w in watchers)
        [cam] = now["cameras"]
        assert cam["name"] == "cam" and cam["state"] == "streaming" and cam["viewers"] == 2
        assert now["process"]["threads"] > 1 and now["started"] == service.started
        assert client.state()["cameras"][0]["watching"] == 0  # web views aren't "programs"

        last = report["samples"][-1]["t"]
        later = client.call("GET", f"/api/stats?since={last}")[1]["samples"]
        assert all(sample["t"] > last for sample in later)
        assert client.call("GET", "/api/stats?since=soon")[0] == 400
    finally:
        page.stop()
        service.stop()


def test_built_files_are_served(app: tuple[Client, ConfigFile]) -> None:
    client, _ = app
    html = client.call("GET", "/")[1].decode()
    script = html.split('src="', 1)[1].split('"', 1)[0]  # /assets/index-<hash>.js
    code, body, head = client.call("GET", script)
    assert code == 200 and head["Content-Type"].startswith("text/javascript") and body
    assert "immutable" in head["Cache-Control"]  # names change with the content
    for path in ("/assets/nope.js", "/assets/..%2Fserver.py", "/assets/index.txt"):
        assert client.call("GET", path)[0] == 404


def test_viewers_on_other_devices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Camera links can be shared with other devices while the page stays on this computer."""
    from faicam import access

    ui = UIConfig(port=free_port_block(1), links="network", bind="127.0.0.1")
    service, page, _ = start(tmp_path, ui)
    client = Client(page.ui.port)
    try:
        client.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
        client.wait_streaming("cam")
        monkeypatch.setattr(access, "is_loopback", lambda address: False)  # as if from a phone

        assert client.call("GET", "/snapshot/cam.jpg")[0] == 200
        assert client.call("GET", "/live/cam.json")[0] == 200
        code, html, _ = client.call("GET", "/live/cam.mjpg", headers={"Accept": "text/html"})
        assert code == 200 and b'id="video"' in html
        script = html.decode().split('src="', 1)[1].split('"', 1)[0]
        assert client.call("GET", script)[0] == 200  # the viewer page's own files
        code, reply, _ = client.call("GET", "/api/state")
        assert code == 403 and "only opens on the computer running faicam" in reply["error"]
        code, html, _ = client.call("GET", "/", headers={"Accept": "text/html"})
        assert code == 403 and b"only opens on the computer running faicam" in html
        assert client.call("POST", "/api/cameras/cam/pause", {})[0] == 403
        # a website pointing its own name at this computer (DNS rebinding) gets nothing
        rebound = {"Host": f"evil.example:{page.ui.port}"}
        assert client.call("GET", "/snapshot/cam.jpg", headers=rebound)[0] == 403
    finally:
        page.stop()
        service.stop()


def test_camera_links_with_a_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from faicam import access

    ui = UIConfig.model_validate(
        {
            "port": free_port_block(1),
            "bind": "127.0.0.1",
            "links": "network",
            "links_password": "view",
            "page": "network",
            "password": "admin",
        }
    )
    service, page, _ = start(tmp_path, ui)
    admin = Client(page.ui.port, password="admin")
    try:
        admin.call("POST", "/api/cameras", {"name": "cam", **synthetic()})
        admin.wait_streaming("cam")
        monkeypatch.setattr(access, "is_loopback", lambda address: False)

        code, _, head = Client(page.ui.port).call("GET", "/snapshot/cam.jpg")
        assert code == 401 and 'realm="faicam camera links"' in head["WWW-Authenticate"]
        viewer = Client(page.ui.port, password="view")
        assert viewer.call("GET", "/snapshot/cam.jpg")[0] == 200
        assert viewer.call("GET", "/api/state")[0] == 401  # watching only
        assert admin.call("GET", "/snapshot/cam.jpg")[0] == 200  # the page password opens links
    finally:
        page.stop()
        service.stop()
