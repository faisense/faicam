"""faicam's settings file: faistream's, plus cameras given by IP address and brand,
and the web page (``ui``).

    cameras:
      door:
        ip: 192.168.1.21
        username: admin
        password: secret
        brand: hikvision       # faicam knows the stream address of common brands
        backup: yard
      replay:
        source: ./recordings/lobby.mp4   # anything faistream reads: a file, RTSP URL, ...
    ui:
      links: network           # phones and TVs can open camera links

The page edits this file through ``ConfigFile``, keeping ``${VAR}`` references.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from faistream.config import ConfigError, ServiceConfig, StreamConfig, StreamName, Target, validate
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from faicam.brands import BRANDS, normalize_brand, rtsp_url

DEFAULT_UI_PORT = 7380
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class CameraConfig(StreamConfig):
    """A faistream stream that can also be given as a camera: IP address, login, brand."""

    source: Target | None = None  # type: ignore[assignment]  # optional when ``ip`` is set
    ip: str | None = None
    port: int = Field(default=554, ge=1, le=65535)
    username: str = ""
    password: str = ""
    brand: str | None = None
    path: str | None = None

    @field_validator("brand")
    @classmethod
    def _known_brand(cls, value: str | None) -> str | None:
        if value is None or normalize_brand(value) == "other":
            return None if value is None else "other"
        key = normalize_brand(value)
        if key not in BRANDS:
            raise ValueError(
                f"unknown brand; use one of {', '.join(BRANDS)}, or 'other' with a path"
            )
        return key

    @model_validator(mode="after")
    def _camera_or_source(self) -> CameraConfig:
        if (self.ip is None) == (self.source is None):
            raise ValueError("set either 'ip' (a camera) or 'source' (any other input)")
        if self.ip is not None and not self.path and self.brand in (None, "other"):
            raise ValueError(f"set 'brand' ({', '.join(BRANDS)}) or the stream 'path'")
        return self

    @property
    def primary(self) -> Target:
        if self.source is not None:
            return self.source
        assert self.ip is not None
        path = self.path or BRANDS[self.brand or ""].path
        return rtsp_url(self.ip, path, self.username, self.password, self.port)


Sharing = Literal["local", "network"]


class UIConfig(BaseModel):
    """The web page. Who can open the page itself (to add, edit and pause cameras) and
    who can open the camera links viewers use (``/live``, ``/snapshot``) are separate:
    phones and TVs can watch without being able to change anything."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    port: int = Field(default=DEFAULT_UI_PORT, ge=1, le=65535)
    page: Sharing = "local"
    password: str | None = None  # needed to open the page from other machines
    links: Sharing = "local"
    links_password: str | None = None  # optional: viewers on other devices must enter it
    bind: str = "0.0.0.0"  # the interface other machines reach, when anything is shared

    @model_validator(mode="before")
    @classmethod
    def _legacy_host(cls, data: Any) -> Any:
        """Older files set ``host``: any address but localhost opened the page."""
        if isinstance(data, dict) and "host" in data:
            data = dict(data)
            host = str(data.pop("host"))
            if host not in LOOPBACK:
                data.setdefault("page", "network")
                if host not in ("0.0.0.0", "::"):
                    data.setdefault("bind", host)
        return data

    @model_validator(mode="after")
    def _protect_remote_access(self) -> UIConfig:
        if self.page == "network" and not self.password:
            raise ValueError("set 'password' to open the page to other machines")
        return self

    @property
    def host(self) -> str:
        """Where the web server listens: only this computer unless something is shared."""
        return self.bind if "network" in (self.page, self.links) else "127.0.0.1"


class FaicamConfig(ServiceConfig):
    """faistream's settings with cameras and the web page. faistream's ``Service`` runs it."""

    streams: dict[StreamName, CameraConfig] = Field(  # type: ignore[assignment]
        default_factory=dict, validation_alias=AliasChoices("cameras", "streams")
    )
    ui: UIConfig | None = Field(default_factory=UIConfig)

    @field_validator("ui", mode="before")
    @classmethod
    def _ui_flag(cls, value: Any) -> Any:
        if value is True:
            return {}
        if value is False:
            return None
        return value


def parse_config(data: Any) -> FaicamConfig:
    return validate(FaicamConfig, data)


_HEADER = """\
# faistream cameras. The web page edits this file; you can also edit it by hand.
# Keep it private: it contains camera passwords.
"""


class ConfigFile:
    """The config file on disk, as edited by the web page.

    Changes are made to the document as written (not the parsed config), so
    ``${VAR}`` references and settings the page doesn't show survive a save.
    """

    def __init__(self, path: str | Path, raw: dict[str, Any]):
        self.path = Path(path)
        self.raw = raw
        self.overrides: dict[str, Any] = {}  # command-line settings, applied but never saved

    @classmethod
    def open(cls, path: str | Path, *, create: bool = True) -> ConfigFile:
        path = Path(path)
        if not path.exists():
            if not create:
                raise ConfigError(f"config file not found: {path}")
            return cls(path, {"cameras": {}})
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from None
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} should contain settings like 'cameras:'")
        return cls(path, raw)

    def config(self) -> FaicamConfig:
        return self.parse(self.raw)

    def parse(self, raw: dict[str, Any]) -> FaicamConfig:
        return parse_config({**raw, **self.overrides})

    @property
    def _key(self) -> str:
        return "streams" if "streams" in self.raw and "cameras" not in self.raw else "cameras"

    def cameras(self) -> dict[str, dict[str, Any]]:
        return dict(self.raw.get(self._key) or {})

    def with_camera(
        self, name: str, entry: dict[str, Any], *, replaces: str | None = None
    ) -> dict[str, Any]:
        """The document with camera ``name`` added, or replacing ``replaces`` (maybe renamed)."""
        cameras = self.cameras()
        if replaces is None and name in cameras:
            raise ConfigError(f"a camera named {name!r} already exists")
        if replaces is not None and replaces != name and name in cameras:
            raise ConfigError(f"a camera named {name!r} already exists")
        updated: dict[str, Any] = {}
        for key, value in cameras.items():
            if key == replaces:
                updated[name] = entry
            else:
                updated[key] = _rename_backup(value, replaces, name) if replaces else value
        if replaces is None or replaces not in cameras:
            updated[name] = entry
        return {**self.raw, self._key: updated}

    def without_camera(self, name: str) -> dict[str, Any]:
        cameras = self.cameras()
        if name not in cameras:
            raise ConfigError(f"no camera named {name!r}")
        updated = {k: _rename_backup(v, name, None) for k, v in cameras.items() if k != name}
        return {**self.raw, self._key: updated}

    def save(self, raw: dict[str, Any]) -> None:
        """Write ``raw`` atomically, readable only by the current user."""
        text = _HEADER + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        self.raw = raw


def _rename_backup(entry: Any, old: str | None, new: str | None) -> Any:
    """Point backups that name camera ``old`` at ``new``, or drop them if ``new`` is None."""
    if not isinstance(entry, dict) or "backup" not in entry or old is None:
        return entry
    backups = entry["backup"] if isinstance(entry["backup"], list) else [entry["backup"]]
    kept = [new if b == old else b for b in backups if not (b == old and new is None)]
    updated = {k: v for k, v in entry.items() if k != "backup"}
    if kept:
        updated["backup"] = kept if len(kept) > 1 else kept[0]
    return updated
