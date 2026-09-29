"""Editing the configuration from the web page.

Turns what the page's forms send into changes to ``cameras.yaml``, and back. It
knows nothing about HTTP: ``server.py`` serves it. Rules it keeps:

- The file changes only after the new configuration validates.
- Camera changes apply to the running service at once; service-wide ones
  (network, ports, the page itself) restart faicam.
- Passwords never go back to the browser. The YAML editor shows them as
  ``********`` and puts the real ones back when saving.
"""

from __future__ import annotations

import copy
import logging
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit

import numpy as np
import yaml
from faistream.config import (
    DEFAULT_PORT,
    ENV_REFERENCE,
    ConfigError,
    NetworkConfig,
    expand_env,
)
from faistream.streaming.service import Service, ServiceError
from faistream.streaming.sources import (
    add_credentials,
    is_stream_address,
    missing_file,
    redact,
    split_credentials,
)

from faicam.brands import BRANDS, DetectError, detect, probe_stream
from faicam.config import (
    DEFAULT_UI_PORT,
    LOOPBACK,
    CameraConfig,
    ConfigFile,
    FaicamConfig,
    UIConfig,
)
from faicam.rtsp_check import diagnose

log = logging.getLogger(__name__)

MASK = "********"
_FORM_FIELDS = (
    "ip", "port", "username", "password", "brand", "path", "source", "backup",
    "fps", "size", "rtsp_transport", "open_timeout", "read_timeout",
)  # fmt: skip
_DEFAULT_TIMEOUT = 5.0


class EditError(Exception):
    """A change the page can't make; ``message`` is shown to the user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def page_url(ui: UIConfig) -> str:
    """Where the page opens: localhost, or the address other machines use when shared."""
    host = display_host(ui.bind) if ui.page == "network" else "localhost"
    return f"http://{host}:{ui.port}"


def links_url(ui: UIConfig) -> str:
    """The start of every camera link, as other devices reach it when they may."""
    host = display_host(ui.bind) if "network" in (ui.page, ui.links) else "localhost"
    return f"http://{host}:{ui.port}"


def display_host(host: str) -> str:
    if host in LOOPBACK:
        return "localhost"
    if host in ("0.0.0.0", "::"):
        try:  # the address other machines would use; nothing is sent
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                return str(s.getsockname()[0])
        except OSError:
            return socket.gethostname()
    return host


class Editor:
    def __init__(
        self,
        service: Service,
        file: ConfigFile | None,
        restart: Callable[[], None] | None = None,
    ):
        self.service = service
        self.file = file
        self._restart = restart

    def _config(self) -> FaicamConfig:
        config = self.service.config
        assert isinstance(config, FaicamConfig)  # faicam always runs the service with its own
        return config

    def _camera(self, name: str) -> CameraConfig:
        return self._config().streams[name]

    # -- what the forms show -------------------------------------------------

    def camera_settings(self, name: str) -> dict[str, Any]:
        """The camera form's fields. Never includes passwords."""
        cfg = self._camera(name)
        raw = (self.file.cameras().get(name) if self.file else None) or {}
        kind = "camera" if cfg.ip else "source"
        username, has_password = cfg.username, bool(raw.get("password") or cfg.password)
        source = redact(cfg.source) if cfg.source is not None else ""
        if cfg.source is not None and is_stream_address(cfg.source):
            kind = "rtsp"  # shown with the username and password in their own fields
            source, username, password = split_credentials(str(raw.get("source", cfg.source)))
            has_password = bool(password)
        return {
            "kind": kind,
            "ip": cfg.ip or "",
            "port": cfg.port,
            "username": username,
            "has_password": has_password,
            "brand": cfg.brand or ("other" if cfg.ip else ""),
            "path": cfg.path or "",
            "source": source,
            "backups": self._backups_shown(raw.get("backup", cfg.backup)),
            "fps": cfg.fps,
            "size": f"{cfg.size[0]}x{cfg.size[1]}" if cfg.size else "",
            "rtsp_transport": cfg.rtsp_transport,
            "paused": cfg.paused,
            "open_timeout": cfg.open_timeout,
            "read_timeout": cfg.read_timeout,
        }

    def service_settings(self) -> dict[str, Any]:
        """The Settings panel's fields: everything that isn't a camera."""
        config = self._config()
        network = config.network or NetworkConfig()
        ui = config.ui or UIConfig()
        return {
            "network": {
                "enabled": config.network is not None,
                "codec": network.codec,
                "quality": network.quality,
                "level": network.level,
                "bind": network.bind,
            },
            "ui": {
                "page": ui.page,
                "links": ui.links,
                "port": ui.port,
                "has_password": bool(ui.password),
                "has_links_password": bool(ui.links_password),
            },
            "port": config.port,
            "slots": config.slots,
            "overridden": sorted(self.file.overrides) if self.file else [],
            "editable": self.file is not None,
        }

    def _backups_shown(self, value: Any) -> list[dict[str, str]]:
        names = set(self.service.config.streams)
        items = value if isinstance(value, list) else [] if value is None else [value]
        shown = []
        for item in items:
            if isinstance(item, str) and item in names:
                shown.append({"type": "camera", "value": item})
            else:
                text = str(item)
                if is_stream_address(text):
                    text = split_credentials(text)[0]
                shown.append({"type": "address", "value": text})
        return shown

    # -- testing a camera --------------------------------------------------------

    def probe(self, data: dict[str, Any]) -> tuple[np.ndarray, str | None, str | None]:
        """Connect to what the form describes; returns one frame, the brand and the path."""
        kind = data.get("kind")
        if kind == "rtsp":
            url = self._expand(self._url_field(data))
            try:
                return probe_stream(url, 8.0), None, None
            except Exception:
                raise EditError(422, _explain_failure(url)) from None
        if kind == "source":
            target = self._source_field(data)
            try:
                return (
                    probe_stream(str(target) if isinstance(target, int) else target, 5.0),
                    None,
                    None,
                )
            except Exception as exc:
                raise EditError(422, f"Couldn't open this source: {exc}") from None
        ip, port, username, password, brand, path = self._camera_fields(data)
        try:
            found = detect(ip, username, password, brand=brand, path=path, port=port)
        except DetectError as exc:
            raise EditError(422, str(exc)) from None
        return found.image, found.brand, found.path

    # -- saving ------------------------------------------------------------------

    def save_camera(self, data: dict[str, Any]) -> dict[str, Any]:
        file = self._require_file()
        name = str(data.get("name", "")).strip()
        original = str(data.get("original") or "").strip() or None
        existing = file.cameras().get(original, {}) if original else {}
        if not isinstance(existing, dict):
            existing = {}
        entry: dict[str, Any] = {}
        kind = data.get("kind", "camera")
        if kind == "camera":
            ip, port, username, _, brand, path = self._camera_fields(data)
            if brand is None and path is None:  # "find automatically"
                _, brand, path = self.probe(data)
                path = None if brand else path
            entry["ip"] = ip
            if port != 554:
                entry["port"] = port
            if username:
                entry["username"] = username
            stored_password = data.get("password") or existing.get("password") or ""
            if stored_password:
                entry["password"] = stored_password  # as written, so ${VAR} survives
            entry["brand"] = brand or "other"
            if path and (brand is None or path != BRANDS[brand].path):
                entry["path"] = path
        elif kind == "rtsp":
            entry["source"] = self._url_field(data)
        else:
            entry["source"] = self._source_field(data)

        backups = self._backups_field(data, name, original, existing.get("backup"))
        if backups:
            entry["backup"] = backups[0] if len(backups) == 1 else backups
        if data.get("fps"):
            entry["fps"] = _number(data["fps"], "Frame rate limit")
        if str(data.get("size") or "").strip():
            entry["size"] = str(data["size"]).strip().lower().replace(" ", "")
        if data.get("rtsp_transport") in ("tcp", "udp"):
            entry["rtsp_transport"] = data["rtsp_transport"]
        for key, label in (
            ("open_timeout", "Connect timeout"),
            ("read_timeout", "No-picture timeout"),
        ):
            if data.get(key) not in (None, "") and _number(data[key], label) != _DEFAULT_TIMEOUT:
                entry[key] = _number(data[key], label)
        # Anything the form doesn't know about stays as it was.
        entry.update({k: v for k, v in existing.items() if k not in _FORM_FIELDS})
        try:
            raw = file.with_camera(name, entry, replaces=original)
        except ConfigError as exc:
            raise EditError(400, _friendly(str(exc))) from None
        return self._commit(raw)

    def set_paused(self, name: str, paused: bool) -> dict[str, Any]:
        """Stop a camera's streaming until it's resumed (kept in the file, so restarts keep it)."""
        file = self._require_file()
        current = file.cameras().get(name)
        if current is None:
            raise EditError(404, f"No camera named {name!r}.")
        entry = dict(current) if isinstance(current, dict) else {}
        if paused:
            entry["paused"] = True
        else:
            entry.pop("paused", None)
        return self._commit(file.with_camera(name, entry, replaces=name))

    def remove_camera(self, name: str) -> dict[str, Any]:
        file = self._require_file()
        try:
            raw = file.without_camera(name)
        except ConfigError as exc:
            raise EditError(404, _friendly(str(exc))) from None
        return self._commit(raw)

    def save_service_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        file = self._require_file()
        raw = dict(file.raw)
        net = data.get("network") or {}
        if net.get("enabled"):
            network: dict[str, Any] = {"codec": net.get("codec") or "jpeg"}
            if network["codec"] == "jpeg":
                network["quality"] = int(_number(net.get("quality") or 85, "JPEG quality"))
            if network["codec"] == "zstd":
                network["level"] = int(_number(net.get("level") or 3, "zstd level"))
            if (net.get("bind") or "0.0.0.0").strip() != "0.0.0.0":
                network["bind"] = net["bind"].strip()
            raw["network"] = True if network == {"codec": "jpeg", "quality": 85} else network
        else:
            raw.pop("network", None)

        form = data.get("ui") or {}
        current_ui = raw.get("ui")
        old_ui: dict[str, Any] = current_ui if isinstance(current_ui, dict) else {}
        ui: dict[str, Any] = {}
        port = int(_number(form.get("port") or DEFAULT_UI_PORT, "Web page port"))
        if port != DEFAULT_UI_PORT:
            ui["port"] = port
        if form.get("page") == "network":
            ui["page"] = "network"
        password = form.get("password") or old_ui.get("password")
        if password:
            ui["password"] = password
        if form.get("page") == "network" and not password:
            raise EditError(400, "Set a password to open the page to other machines.")
        if form.get("links") == "network":
            ui["links"] = "network"
        if form.get("links_protected"):
            links_password = form.get("links_password") or old_ui.get("links_password")
            if not links_password:
                raise EditError(400, "Type a password for camera links, or turn that option off.")
            ui["links_password"] = links_password
        bind = old_ui.get("bind") or old_ui.get("host")  # older files: host
        if isinstance(bind, str) and bind not in (*LOOPBACK, "0.0.0.0"):
            ui["bind"] = bind
        if ui:
            raw["ui"] = ui
        else:
            raw.pop("ui", None)

        for key, label, default in (
            ("port", "Control port", DEFAULT_PORT),
            ("slots", "Frames kept", 4),
        ):
            value = int(_number(data.get(key) or default, label))
            if value != default:
                raw[key] = value
            else:
                raw.pop(key, None)
        return self._commit(raw)

    def yaml_text(self) -> str:
        """The whole file for the advanced editor, with passwords masked."""
        file = self._require_file()
        return yaml.safe_dump(_mask(file.raw), sort_keys=False, allow_unicode=True)

    def save_yaml(self, text: str) -> dict[str, Any]:
        file = self._require_file()
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" on line {mark.line + 1}" if mark is not None else ""
            problem = getattr(exc, "problem", None) or str(exc)
            raise EditError(400, f"This isn't valid YAML{where}: {problem}.") from None
        if not isinstance(data, dict):
            raise EditError(400, "The settings should start with keys like 'cameras:'.")
        return self._commit(_unmask(data, file.raw))

    # -- applying ------------------------------------------------------------------

    def _commit(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Validate, save, then apply live or restart; the old file comes back on failure."""
        file = self._require_file()
        try:
            config = file.parse(raw)
        except ConfigError as exc:
            raise EditError(400, _friendly(str(exc))) from None
        restart = _service_wide(config) != _service_wide(self._config())
        previous = file.raw
        try:
            file.save(raw)
        except OSError as exc:
            raise EditError(
                500, f"Could not save {file.path}: {exc.strerror}. Is the file read-only?"
            ) from None
        if restart:
            if self._restart is None:
                return {"ok": True, "restart": False, "note": "Saved. Restart faicam to apply."}
            log.info("settings changed; restarting to apply them")
            self._restart()
            return {"ok": True, "restart": True, "url": page_url(config.ui) if config.ui else None}
        try:
            self.service.apply(config)
        except ServiceError as exc:
            file.save(previous)
            raise EditError(409, str(exc)) from None
        return {"ok": True, "restart": False}

    # -- form fields -------------------------------------------------------------------

    def _require_file(self) -> ConfigFile:
        if self.file is None:
            raise EditError(
                409, "Cameras can be edited when faicam runs from a config file: faicam"
            )
        return self.file

    def _existing(self, data: dict[str, Any]) -> dict[str, Any]:
        original = str(data.get("original") or "")
        entry = (self.file.cameras().get(original) if self.file and original else None) or {}
        return entry if isinstance(entry, dict) else {}

    @staticmethod
    def _expand(value: str) -> str:
        try:
            return str(expand_env(value))
        except ConfigError as exc:
            raise EditError(400, str(exc)) from None

    def _camera_fields(
        self, data: dict[str, Any]
    ) -> tuple[str, int, str, str, str | None, str | None]:
        ip = str(data.get("ip", "")).strip()
        if not ip:
            raise EditError(400, "Enter the camera's IP address.")
        port = int(_number(data.get("port") or 554, "Port"))
        username = str(data.get("username", "")).strip()
        password = str(data.get("password") or "") or str(
            self._existing(data).get("password") or ""
        )
        brand = str(data.get("brand") or "auto")
        path = str(data.get("path") or "").strip() or None
        if brand == "other" and not path:
            raise EditError(400, "Enter the stream path from the camera's manual.")
        known = None if brand in ("auto", "other") else brand
        return ip, port, username, self._expand(password), known, path

    def _url_field(self, data: dict[str, Any]) -> str:
        """The stream address from the form, with the username and password put into it."""
        url = str(data.get("source") or "").strip()
        if not url:
            raise EditError(
                400, "Enter the stream address, for example rtsp://192.168.1.21:554/live."
            )
        if not is_stream_address(url):
            raise EditError(
                400,
                "The address should start with rtsp:// (https://, rtmp:// and srt:// work too).",
            )
        base, username, password = split_credentials(url)
        username = str(data.get("username") or "").strip() or username
        password = str(data.get("password") or "") or password
        old = self._existing(data).get("source")
        if not password and isinstance(old, str) and is_stream_address(old):
            password = split_credentials(old)[2]  # left empty on the form: keep it
        return add_credentials(base, username, password)

    def _source_field(self, data: dict[str, Any]) -> str | int:
        """The source from the form; a missing local file is reported right away."""
        source = str(data.get("source", "")).strip()
        if not source:
            raise EditError(
                400, "Enter the source: a video file, a USB camera number or a test pattern."
            )
        old = self._existing(data).get("source")
        if old is not None and source in (redact(old), split_credentials(str(old))[0]):
            source = str(old)  # unchanged, shown without its password: keep the real one
        _check_file(source)
        return int(source) if source.isdigit() else source

    def _backups_field(
        self, data: dict[str, Any], name: str, original: str | None, old: Any
    ) -> list[str | int]:
        """Backups in order: other cameras' names, or addresses and files."""
        cameras = set(self.file.cameras()) if self.file else set()
        old_items = old if isinstance(old, list) else [] if old is None else [old]
        backups: list[str | int] = []
        for item in data.get("backups") or []:
            value = str(item.get("value", "")).strip() if isinstance(item, dict) else ""
            if not value:
                continue
            if item.get("type") == "camera":
                if value in (name, original):
                    raise EditError(400, "A camera can't be its own backup.")
                if value not in cameras:
                    raise EditError(400, f"There's no camera named {value!r} to use as a backup.")
                backups.append(value)
                continue
            for previous in old_items:  # shown without its password: keep the real one
                if isinstance(previous, str) and value in (
                    redact(previous),
                    split_credentials(previous)[0],
                ):
                    value = previous
            _check_file(value)
            backups.append(int(value) if value.isdigit() else value)
        return backups


def _service_wide(config: FaicamConfig) -> tuple[Any, ...]:
    """The settings that can only change with a restart."""
    return config.network, config.port, config.slots, config.ui


def _check_file(source: str) -> None:
    missing = missing_file(source)
    if missing:
        raise EditError(
            400, f"There's no file at {missing}. The path is on the computer running faicam."
        )


def _explain_failure(url: str) -> str:
    """Why a stream address didn't open, in words the user can act on."""
    if url.startswith("rtsp://"):
        reason = diagnose(url)
        if reason.kind == "ok":
            return (
                "The server accepts the stream, but its video couldn't be decoded. It may use a "
                "format this computer's OpenCV can't read."
            )
        if reason.message:
            return reason.message
    return "Couldn't open this address. Check it, and that the stream is live right now."


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EditError(400, f"{label} must be a number.") from None
    if number <= 0:
        raise EditError(400, f"{label} must be more than 0.")
    return int(number) if number.is_integer() else number


def _friendly(message: str) -> str:
    """Config errors, reworded for the page. Each keeps where it is, e.g. ``cameras.door.fps``."""
    message = message.removeprefix("invalid configuration:\n").strip()
    replacements = {
        "String should match pattern '^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$'": (
            "use letters, numbers, dots, dashes or underscores, starting with a letter or number"
        ),
        "Extra inputs are not permitted": "unknown setting (check the spelling)",
    }
    for technical, plain in replacements.items():
        message = message.replace(technical, plain)
    return "\n".join(line.strip() for line in message.splitlines() if line.strip())


# -- hiding passwords in the YAML editor ----------------------------------------------


_UI_SECRETS = {"password": "web page password", "links_password": "camera links password"}


def _is_secret(value: Any) -> bool:
    return isinstance(value, str) and value != "" and not ENV_REFERENCE.fullmatch(value)


def _cameras_of(doc: dict[str, Any]) -> dict[str, Any]:
    cameras = doc.get("cameras", doc.get("streams"))
    return cameras if isinstance(cameras, dict) else {}


def _mask_address(value: Any) -> Any:
    if isinstance(value, list):
        return [_mask_address(v) for v in value]
    if not (isinstance(value, str) and is_stream_address(value)):
        return value
    parts = urlsplit(value)
    if parts.password is None or not _is_secret(unquote(parts.password)):
        return value
    return value.replace(f":{parts.password}@", f":{MASK}@", 1)


def _mask(doc: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(doc)
    for entry in _cameras_of(doc).values():
        if not isinstance(entry, dict):
            continue
        if _is_secret(entry.get("password")):
            entry["password"] = MASK
        for key in ("source", "backup"):
            if key in entry:
                entry[key] = _mask_address(entry[key])
    ui = doc.get("ui")
    if isinstance(ui, dict):
        for key in _UI_SECRETS:
            if _is_secret(ui.get(key)):
                ui[key] = MASK
    return doc


def _addresses(doc: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for entry in _cameras_of(doc).values():
        if isinstance(entry, dict):
            for key in ("source", "backup"):
                values = entry.get(key)
                for value in values if isinstance(values, list) else [values]:
                    if isinstance(value, str) and is_stream_address(value):
                        found.append(value)
    return found


def _unmask(doc: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """Put the real passwords back where the editor still shows ``********``."""
    doc = copy.deepcopy(doc)
    old_cameras = _cameras_of(old)
    old_addresses = _addresses(old)

    def address(value: Any) -> Any:
        if isinstance(value, list):
            return [address(v) for v in value]
        if not (isinstance(value, str) and f":{MASK}@" in value):
            return value
        base, username, _ = split_credentials(value.replace(MASK, "x"))
        for candidate in old_addresses:
            if split_credentials(candidate)[:2] == (base, username):
                return candidate
        raise EditError(
            400, f"Type the password in {base} again; faicam can't tell which one it was."
        )

    for name, entry in _cameras_of(doc).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("password") == MASK:
            previous = old_cameras.get(name)
            if not (isinstance(previous, dict) and previous.get("password")):
                raise EditError(
                    400, f"Type the password for {name!r} again; it's a new or renamed camera."
                )
            entry["password"] = previous["password"]
        for key in ("source", "backup"):
            if key in entry:
                entry[key] = address(entry[key])
    ui, old_ui = doc.get("ui"), old.get("ui")
    for key, label in _UI_SECRETS.items():
        if isinstance(ui, dict) and ui.get(key) == MASK:
            if not (isinstance(old_ui, dict) and old_ui.get(key)):
                raise EditError(400, f"Type the {label} again.")
            ui[key] = old_ui[key]
    return doc
