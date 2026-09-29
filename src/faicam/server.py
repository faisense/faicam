"""The web server: the cameras page, the stats page, their API, and camera links.

``faicam`` runs it at http://localhost:7380, next to faistream's service. The pages
are built from ``frontend/`` into ``static/`` and ship in this package, so nothing is
fetched from the internet. Camera passwords are never sent to the browser. Who may open
what is in ``access.py``; the editing rules are in ``editor.py``.

Every camera also has plain web addresses that any browser or tool can open:

    /live/<name>.mjpg       live video (MJPEG): browsers, VLC, OBS, cv2.VideoCapture, YOLO
    /snapshot/<name>.jpg    the latest picture
    /live/<name>.json       whether it's streaming or paused (the viewer page asks)

Both pictures take ``?width=`` and ``?quality=``; ``/live`` also takes ``?fps=``. A
browser opening ``/live`` directly gets a full-screen viewer page instead of the raw
stream: Safari (iPhone and Mac) never finishes loading a bare MJPEG document.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import string
import threading
import time
from collections.abc import Callable
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import cv2
import numpy as np
from faistream.client import Subscriber
from faistream.streaming.service import Service, ServiceError
from faistream.streaming.sources import redact

from faicam import access
from faicam.brands import BRANDS
from faicam.config import ConfigFile, UIConfig
from faicam.editor import EditError, Editor, links_url, page_url
from faicam.stats import Stats, WebView, app_name

log = logging.getLogger(__name__)

_LIVE_FPS = 15  # defaults for /live; ?fps= and ?width= change them
_LIVE_WIDTH = 1280
_SNAPSHOT_WIDTH = 1920
_VIEWER_IDLE = 10.0
_MAX_BODY = 1024 * 1024
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
_ASSET = re.compile(r"^[A-Za-z0-9_.-]+$")
# The built pages load only their own files; nothing inline, nothing from elsewhere.
_PAGE_POLICY = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


class _Viewer:
    """A subscriber the page reuses for thumbnails while it is open."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.subscriber = Subscriber(endpoint)
        self.lock = threading.Lock()
        self.used = time.monotonic()


class WebUI:
    def __init__(
        self,
        service: Service,
        ui: UIConfig,
        config_file: ConfigFile | None = None,
        restart: Callable[[], None] | None = None,
    ):
        self.service = service
        self.ui = ui
        self.file = config_file
        self._restart = restart
        self.editor = Editor(service, config_file, self._schedule_restart if restart else None)
        self._viewers: dict[str, _Viewer] = {}
        self.stats = Stats(service)
        # The latest JPEG per (camera, width, quality), so live viewers share one encode.
        self._encoded: dict[tuple[str, int, int], tuple[int, int, bytes]] = {}
        self._encoding: dict[tuple[str, int, int], threading.Lock] = {}
        # Live views the page asked to end (by id). Browsers such as Safari keep an
        # MJPEG connection open after the picture is replaced, so the server closes it.
        self._stop_requests: dict[str, float] = {}
        self._lock = threading.Lock()
        self._edit_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def url(self) -> str:
        return page_url(self.ui)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        try:
            self._server = ThreadingHTTPServer((self.ui.host, self.ui.port), _handler(self))
        except OSError as exc:
            raise ServiceError(
                f"cannot open the web page on port {self.ui.port} ({exc.strerror}); "
                "set 'ui: {port: ...}' to another number"
            ) from None
        self._server.daemon_threads = True
        workers: list[tuple[Callable[[], None], str]] = [
            (self._serve, "web"),
            (self._janitor, "web-janitor"),
            (lambda: self.stats.run(self._stopping), "web-stats"),
        ]
        for target, name in workers:
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stopping.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        with self._lock:
            viewers = list(self._viewers.values())
            self._viewers.clear()
        for viewer in viewers:
            with viewer.lock:
                viewer.subscriber.close()

    def _serve(self) -> None:
        assert self._server is not None
        self._server.serve_forever(poll_interval=0.25)

    def _janitor(self) -> None:
        """Close thumbnail viewers the page stopped using, so idle streams stop decoding."""
        while not self._stopping.wait(2):
            now = time.monotonic()
            with self._lock:
                idle = [n for n, v in self._viewers.items() if now - v.used > _VIEWER_IDLE]
                closing = [self._viewers.pop(n) for n in idle]
                for stream_id, asked in list(self._stop_requests.items()):
                    if now - asked > 60:  # nobody was streaming under that id
                        del self._stop_requests[stream_id]
            for viewer in closing:
                with viewer.lock:
                    viewer.subscriber.close()

    def _schedule_restart(self) -> None:
        # Give the page a moment to receive the answer before everything stops.
        assert self._restart is not None
        threading.Timer(0.6, self._restart).start()

    # -- what the page shows -----------------------------------------------

    def state(self) -> dict[str, Any]:
        config = self.service.config
        status = {s["name"]: s for s in self.service.describe()}
        audience = self.service.audience()
        cameras = []
        for name, cfg in config.streams.items():
            st = status.get(name, {})
            cameras.append(
                {
                    "name": name,
                    "state": st.get("state", "stopped"),
                    "on_backup": st.get("on_backup", False),
                    "fps": st.get("fps", 0.0),
                    "width": st.get("width", 0),
                    "height": st.get("height", 0),
                    "error": st.get("error"),
                    "reconnects": st.get("reconnects", 0),
                    "watching": _programs(audience.get(name)),
                    "source": st.get("source") or redact(cfg.primary),
                    "settings": self.editor.camera_settings(name),
                }
            )
        return {
            "cameras": cameras,
            "brands": [{"key": k, "label": b.label} for k, b in BRANDS.items()],
            "editable": self.file is not None,
            "config_path": str(self.file.path.resolve()) if self.file else None,
            "service": self.editor.service_settings(),
            # Where other devices reach camera links, for links people copy.
            "links": {
                "base": links_url(self.ui),
                "shared": self.ui.links == "network",
                "page_shared": self.ui.page == "network",
                "password": bool(self.ui.links_password),
            },
        }

    def live_status(self, name: str) -> dict[str, Any]:
        """For the viewer page: is the camera streaming, paused, or not there at all."""
        self._endpoint(name)
        status = next((s for s in self.service.describe() if s["name"] == name), None)
        return {"ok": True, "name": name, "state": status["state"] if status else "stopped"}

    # -- editing (rules in editor.py) ---------------------------------------------

    def detect(self, data: dict[str, Any]) -> dict[str, Any]:
        image, brand, path = self.editor.probe(data)
        preview = base64.b64encode(self._jpeg(image, 480, 75)).decode()
        return {
            "ok": True,
            "brand": brand,
            "brand_label": BRANDS[brand].label if brand in BRANDS else None,
            "path": path,
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "preview": f"data:image/jpeg;base64,{preview}",
        }

    def edit(self, change: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._edit_lock:  # one change at a time
            return change()

    # -- pictures --------------------------------------------------------------

    def snapshot(self, name: str, width: int, quality: int = 80) -> bytes:
        self._refuse_if_paused(name)
        endpoint = self._endpoint(name)
        with self._lock:
            viewer = self._viewers.get(name)
            if viewer is None or viewer.endpoint != endpoint:  # new, or the camera restarted
                old = viewer
                viewer = self._viewers[name] = _Viewer(endpoint)
            else:
                old = None
        if old is not None:
            with old.lock:
                old.subscriber.close()
        with viewer.lock:
            viewer.used = time.monotonic()
            try:
                frame = viewer.subscriber.recv(timeout=3)
            except TimeoutError:
                raise EditError(503, "No picture yet.") from None
        return self._jpeg(frame.image, width, quality)

    def live(
        self,
        name: str,
        handler: BaseHTTPRequestHandler,
        fps: float = _LIVE_FPS,
        width: int = _LIVE_WIDTH,
        quality: int = 80,
        stream_id: str | None = None,
    ) -> None:
        self._refuse_if_paused(name)
        endpoint = self._endpoint(name)
        subscriber = Subscriber(endpoint)
        view = WebView(
            camera=name,
            address=str(handler.client_address[0]).removeprefix("::ffff:"),
            app=app_name(handler.headers.get("User-Agent", "")),
            panel=stream_id is not None,  # only the control panel names its live views
        )
        view_id = self.stats.open_view(view)
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            next_frame = 0.0
            while not self._stopping.is_set():
                if stream_id is not None and self._stop_requested(stream_id):
                    return  # the viewer closed; ends the connection
                if self.is_paused(name):
                    return  # the camera was paused from the control panel
                current = self.service.private_endpoint(name)
                if current is None:
                    return  # camera removed
                if current != subscriber.endpoint:  # camera restarted after an edit
                    subscriber.close()
                    subscriber = Subscriber(current)
                try:
                    frame = subscriber.recv(timeout=1)
                except TimeoutError:
                    continue
                now = time.monotonic()
                if now < next_frame:
                    continue
                next_frame = now + 1 / fps
                jpeg = self._shared_jpeg(name, frame, width, quality)
                part = (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n"
                    % (
                        len(jpeg),
                        jpeg,
                    )
                )
                handler.wfile.write(part)
                handler.wfile.flush()
                view.bytes += len(part)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            subscriber.close()
            self.stats.close_view(view_id)

    def _shared_jpeg(self, name: str, frame: Any, width: int, quality: int) -> bytes:
        """Encode each frame once for all live viewers asking for the same size and quality."""
        key = (name, width, quality)
        with self._lock:
            lock = self._encoding.setdefault(key, threading.Lock())
        with lock:  # viewers that get the same frame at once wait for one encode
            cached = self._encoded.get(key)
            if cached is not None and cached[:2] == (frame.epoch, frame.seq):
                return cached[2]
            jpeg = self._jpeg(frame.image, width, quality)
            self._encoded[key] = (frame.epoch, frame.seq, jpeg)
            return jpeg

    def stop_stream(self, stream_id: str) -> None:
        with self._lock:
            self._stop_requests[stream_id] = time.monotonic()

    def _stop_requested(self, stream_id: str) -> bool:
        with self._lock:
            return self._stop_requests.pop(stream_id, None) is not None

    def is_paused(self, name: str) -> bool:
        stream = self.service.config.streams.get(name)
        return bool(stream and stream.paused)

    def _refuse_if_paused(self, name: str) -> None:
        if self.is_paused(name):
            raise EditError(
                503, f"{name} is paused. Resume it in the control panel to stream again."
            )

    def snapshot_check(self, name: str) -> None:
        """Raise the page's usual error if there's no such camera."""
        self._endpoint(name)

    def _endpoint(self, name: str) -> str:
        endpoint = self.service.private_endpoint(name)
        if endpoint is None:
            raise EditError(404, f"No camera named {name!r}.")
        return endpoint

    @staticmethod
    def _jpeg(image: np.ndarray, width: int, quality: int) -> bytes:
        if image.shape[1] > width:
            height = round(image.shape[0] * width / image.shape[1])
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise EditError(500, "Could not encode the picture.")
        return bytes(buf)


def _programs(audience: dict[str, Any] | None) -> int:
    """Programs watching a camera, on this machine or others (the page's own views aside)."""
    return audience["local"] + len(audience["network"]) if audience else 0


def _stream_id(query: dict[str, list[str]]) -> str | None:
    """The id a page gives a live view so it can end it later; letters, digits and dashes."""
    value = query.get("id", [""])[0]
    return value if 8 <= len(value) <= 64 and value.replace("-", "").isalnum() else None


def _stream_name(part: str, extension: str) -> str:
    return part[: -len(extension)] if part.endswith(extension) else part


def _query_int(query: dict[str, list[str]], key: str, default: int, low: int, high: int) -> int:
    raw = query.get(key, [None])[0]
    if raw is None:
        return default
    try:
        value = int(float(raw))
    except ValueError:
        raise EditError(400, f"{key} must be a number from {low} to {high}.") from None
    if not low <= value <= high:
        raise EditError(400, f"{key} must be a number from {low} to {high}.")
    return value


def _query_time(query: dict[str, list[str]]) -> float | None:
    raw = query.get("since", [None])[0]
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise EditError(400, "since must be a time in seconds.") from None


class _Files:
    """The built pages in ``static/`` (from ``frontend/``) and the templates in
    ``templates/``, read once."""

    def __init__(self) -> None:
        self._root = resources.files("faicam")
        self._cache: dict[str, bytes] = {}

    def page(self, name: str) -> bytes:
        body = self._read(f"static/{name}")
        if body is None:
            raise EditError(
                500, "The web pages aren't built. Run `npm run build` in the frontend folder."
            )
        return body

    def asset(self, name: str) -> tuple[bytes, str] | None:
        suffix = name[name.rfind(".") :]
        if not _ASSET.match(name) or suffix not in _CONTENT_TYPES:
            return None
        body = self._read(f"static/assets/{name}")
        return None if body is None else (body, _CONTENT_TYPES[suffix])

    def error_page(self, message: str) -> bytes:
        template = (self._read("templates/error.html") or b"$message").decode()
        return string.Template(template).substitute(message=escape(message)).encode()

    def _read(self, path: str) -> bytes | None:
        if path not in self._cache:
            file = self._root.joinpath(path)
            if not file.is_file():
                return None
            self._cache[path] = file.read_bytes()
        return self._cache[path]


def _handler(app: WebUI) -> type[BaseHTTPRequestHandler]:
    files = _Files()
    editor = app.editor

    class Handler(BaseHTTPRequestHandler):
        server_version = "faicam"

        def log_message(self, format: str, *args: Any) -> None:
            log.debug("web: " + format, *args)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def _dispatch(self, method: str) -> None:
            url = urlsplit(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/")] if url.path != "/" else []
            self._realm = access.PAGE_REALM  # which password a refusal asks for
            try:
                self._check_access(method, parts)
                self._route(method, parts, url.query)
            except EditError as exc:
                self._error(exc.status, exc.message)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception("web: %s %s failed", method, self.path)
                self._error(500, "Something went wrong; see the faicam log.")

        def _route(self, method: str, parts: list[str], query: str) -> None:
            route = (method, *parts[:2])
            if method == "GET" and not parts:
                self._page("index.html")
            elif route == ("GET", "stats") and len(parts) == 1:
                self._page("stats.html")
            elif route[:2] == ("GET", "assets") and len(parts) == 2:
                found = files.asset(parts[1])
                if found is None:
                    raise EditError(404, "Not found.")
                # File names change with their content, so they can be kept for good.
                self._send(
                    200,
                    found[0],
                    found[1],
                    {"Cache-Control": "public, max-age=31536000, immutable"},
                )
            elif route == ("GET", "api", "state"):
                self._json(200, app.state())
            elif route == ("GET", "api", "stats"):
                self._json(200, app.stats.report(_query_time(parse_qs(query))))
            elif route == ("POST", "api", "detect"):
                self._json(200, app.detect(self._body()))
            elif route == ("POST", "api", "cameras") and len(parts) == 2:
                body = self._body()
                self._json(200, app.edit(lambda: editor.save_camera(body)))
            elif (
                route == ("POST", "api", "cameras")
                and len(parts) == 4
                and parts[3] in ("pause", "resume")
            ):
                name, pause = parts[2], parts[3] == "pause"
                self._json(200, app.edit(lambda: editor.set_paused(name, pause)))
            elif route == ("DELETE", "api", "cameras") and len(parts) == 3:
                self._json(200, app.edit(lambda: editor.remove_camera(parts[2])))
            elif route == ("POST", "api", "settings"):
                body = self._body()
                self._json(200, app.edit(lambda: editor.save_service_settings(body)))
            elif route == ("GET", "api", "config"):
                self._json(200, {"ok": True, "yaml": editor.yaml_text()})
            elif route == ("POST", "api", "config"):
                text = str(self._body().get("yaml", ""))
                self._json(200, app.edit(lambda: editor.save_yaml(text)))
            elif route == ("POST", "api", "stop-stream"):
                stream_id = _stream_id(parse_qs(query))
                if stream_id is None:
                    raise EditError(400, "Missing stream id.")
                app.stop_stream(stream_id)
                self._json(200, {"ok": True})
            elif method == "GET" and len(parts) == 2 and parts[0] == "snapshot":
                options = parse_qs(query)
                name = _stream_name(parts[1], ".jpg")
                width = _query_int(options, "width", _SNAPSHOT_WIDTH, 64, 4096)
                quality = _query_int(options, "quality", 80, 1, 100)
                self._send(200, app.snapshot(name, width, quality), "image/jpeg")
            elif method == "GET" and len(parts) == 2 and parts[0] == "live":
                self._live(parts[1], query)
            else:
                raise EditError(404, "Not found.")

        def _live(self, part: str, query: str) -> None:
            if part.endswith(".json"):
                self._json(200, app.live_status(part[: -len(".json")]))
                return
            name = _stream_name(part, ".mjpg")
            if self._wants_page(query):  # a browser opening the link itself
                app.snapshot_check(name)
                self._page("viewer.html")
                return
            options = parse_qs(query)
            app.live(
                name,
                self,
                fps=_query_int(options, "fps", _LIVE_FPS, 1, 60),
                width=_query_int(options, "width", _LIVE_WIDTH, 64, 4096),
                quality=_query_int(options, "quality", 80, 1, 100),
                stream_id=_stream_id(options),
            )

        def _check_access(self, method: str, parts: list[str]) -> None:
            # Camera links, and the files the viewer page needs, are for viewers too.
            kind: access.Kind = (
                "link" if parts[:1] in (["live"], ["snapshot"], ["assets"]) else "page"
            )
            refusal = access.check(
                app.ui,
                kind,
                client=str(self.client_address[0]),
                host=access.host_name(self.headers.get("Host") or ""),
                password=self._password(),
                page_url=app.url,
            )
            if refusal is not None:
                self._realm = refusal.realm
                raise EditError(refusal.status, refusal.message)
            if method != "GET":
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    raise EditError(403, "Changes must come from the faicam page.")

        def _wants_page(self, query: str) -> bool:
            """A browser opening the link itself (not an <img>, VLC or OpenCV asking for video)."""
            accept = self.headers.get("Accept", "")
            return "text/html" in accept and "mjpeg=1" not in query

        def _password(self) -> str | None:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return None
            try:
                _, _, given = base64.b64decode(header[6:]).decode().partition(":")
            except (binascii.Error, UnicodeDecodeError):
                return None
            return given

        def _body(self) -> dict[str, Any]:
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise EditError(415, "Send JSON.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                raise EditError(413, "Request too large.")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                raise EditError(400, "Invalid JSON.") from None
            if not isinstance(data, dict):
                raise EditError(400, "Invalid request.")
            return data

        def _page(self, name: str) -> None:
            self._send(
                200,
                files.page(name),
                _CONTENT_TYPES[".html"],
                {"Content-Security-Policy": _PAGE_POLICY},
            )

        def _error(self, status: int, message: str) -> None:
            extra = {}
            if status == 401:
                extra["WWW-Authenticate"] = f'Basic realm="{self._realm}"'
            if self.command == "GET" and "text/html" in self.headers.get("Accept", ""):
                self._send(status, files.error_page(message), _CONTENT_TYPES[".html"], extra)
            else:
                body = json.dumps({"ok": False, "error": message}).encode()
                self._send(status, body, "application/json", extra)

        def _json(self, status: int, body: dict[str, Any]) -> None:
            self._send(status, json.dumps(body).encode(), "application/json")

        def _send(
            self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None
        ) -> None:
            headers = {"Cache-Control": "no-store", **(extra or {})}
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return Handler
