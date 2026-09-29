"""Editing rules behind the page: settings, backups, the YAML editor, restarts."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from faistream import Service

from faicam.config import ConfigFile, UIConfig
from faicam.editor import MASK, EditError, Editor, _mask, _unmask, links_url, page_url
from tests.helpers import free_port_block, stream_port

SYNTHETIC = {"kind": "source", "source": "synthetic://64x48@30"}


def synthetic() -> dict[str, object]:
    """A new synthetic camera's form fields, with a port of its own."""
    return {**SYNTHETIC, "stream_port": stream_port()}


class Restarts:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Editor, ConfigFile, Restarts]]:
    port = free_port_block()
    path = tmp_path / "cameras.yaml"
    path.write_text(f"port: {port}\ncameras: {{}}\n")
    file = ConfigFile.open(path)
    service = Service(file.config())
    service.start()
    restarts = Restarts()
    try:
        yield Editor(service, file, restarts), file, restarts
    finally:
        service.stop()


def saved(file: ConfigFile) -> dict[str, Any]:
    return yaml.safe_load(file.path.read_text())  # type: ignore[no-any-return]


def test_backups_in_order(setup: tuple[Editor, ConfigFile, Restarts], tmp_path: Path) -> None:
    editor, file, _ = setup
    video = tmp_path / "backup.mp4"
    video.write_bytes(b"")
    editor.save_camera({"name": "a", **synthetic()})
    backups = [
        {"type": "camera", "value": "a"},
        {"type": "address", "value": str(video)},
        {"type": "address", "value": ""},  # empty rows are ignored
    ]
    editor.save_camera({"name": "b", **synthetic(), "backups": backups})
    assert saved(file)["cameras"]["b"]["backup"] == ["a", str(video)]
    assert editor.camera_settings("b")["backups"] == backups[:2]


@pytest.mark.parametrize(
    ("backup", "message"),
    [
        ({"type": "camera", "value": "b"}, "own backup"),
        ({"type": "camera", "value": "ghost"}, "no camera named 'ghost'"),
        ({"type": "address", "value": "~/no-such-backup.mp4"}, "There's no file at"),
    ],
)
def test_backup_errors(
    setup: tuple[Editor, ConfigFile, Restarts], backup: dict[str, str], message: str
) -> None:
    editor, _, _ = setup
    with pytest.raises(EditError, match=message):
        editor.save_camera({"name": "b", **synthetic(), "backups": [backup]})


def test_backup_address_keeps_its_password(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, _ = setup
    secret = "rtsp://u:s3cret@127.0.0.1:1/backup"
    editor.save_camera(
        {"name": "a", **synthetic(), "backups": [{"type": "address", "value": secret}]}
    )
    shown = editor.camera_settings("a")["backups"]
    assert shown == [{"type": "address", "value": "rtsp://127.0.0.1:1/backup"}]
    editor.save_camera({"name": "a", "original": "a", **SYNTHETIC, "backups": shown})
    assert saved(file)["cameras"]["a"]["backup"] == secret


def test_timeouts(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, _ = setup
    editor.save_camera({"name": "a", **synthetic(), "open_timeout": "2.5", "read_timeout": "5"})
    entry = saved(file)["cameras"]["a"]
    assert entry["open_timeout"] == 2.5 and "read_timeout" not in entry  # defaults aren't written
    settings = editor.camera_settings("a")
    assert (settings["open_timeout"], settings["read_timeout"]) == (2.5, 5.0)
    with pytest.raises(EditError, match="Connect timeout must be a number"):
        editor.save_camera({"name": "b", **synthetic(), "open_timeout": "soon"})


def test_camera_changes_apply_without_restart(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, _, restarts = setup
    assert editor.save_camera({"name": "a", **synthetic()}) == {"ok": True, "restart": False}
    assert "a" in editor.service.config.streams and restarts.count == 0


def test_service_settings(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, restarts = setup
    shown = editor.service_settings()
    assert shown["network"]["enabled"] is False
    assert shown["ui"]["page"] == "local" and shown["ui"]["links"] == "local"
    result = editor.save_service_settings(
        {
            "network": {"enabled": True, "codec": "zstd", "level": "5", "bind": "0.0.0.0"},
            "ui": {"page": "network", "password": "pw", "links": "network", "port": "7390"},
            "port": shown["port"],
            "slots": "8",
        }
    )
    assert result["restart"] is True and result["url"].endswith(":7390") and restarts.count == 1
    written = saved(file)
    assert written["network"] == {"codec": "zstd", "level": 5}
    assert written["ui"] == {"port": 7390, "page": "network", "password": "pw", "links": "network"}
    assert written["slots"] == 8


def test_camera_links_can_be_shared_on_their_own(
    setup: tuple[Editor, ConfigFile, Restarts],
) -> None:
    editor, file, _ = setup
    port = editor.service.config.port
    editor.save_service_settings({"ui": {"links": "network"}, "port": port})
    assert saved(file)["ui"] == {"links": "network"}  # the page stays on this computer

    # an optional password for viewers, kept when the field is left empty
    ui = {"links": "network", "links_protected": True, "links_password": "view"}
    editor.save_service_settings({"ui": ui, "port": port})
    assert saved(file)["ui"] == {"links": "network", "links_password": "view"}
    editor.save_service_settings({"ui": {**ui, "links_password": ""}, "port": port})
    assert saved(file)["ui"]["links_password"] == "view"
    editor.save_service_settings({"ui": {"links": "network"}, "port": port})
    assert saved(file)["ui"] == {"links": "network"}  # turned off
    with pytest.raises(EditError, match="password for camera links"):
        editor.save_service_settings({"ui": {**ui, "links_password": ""}, "port": port})


def test_older_files_with_host_keep_working(tmp_path: Path) -> None:
    port = free_port_block()
    (tmp_path / "cameras.yaml").write_text(f"port: {port}\nui:\n  host: 0.0.0.0\n  password: pw\n")
    file = ConfigFile.open(tmp_path / "cameras.yaml")
    service = Service(file.config())
    service.start()
    try:
        editor = Editor(service, file, Restarts())
        assert editor.service_settings()["ui"]["page"] == "network"
        editor.save_service_settings({"ui": {"page": "network"}, "port": port})
        assert saved(file)["ui"] == {"page": "network", "password": "pw"}
    finally:
        service.stop()


def test_remote_page_needs_a_password(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, restarts = setup
    before = file.path.read_text()
    with pytest.raises(EditError, match="Set a password"):
        editor.save_service_settings(
            {"ui": {"page": "network"}, "port": editor.service.config.port}
        )
    assert file.path.read_text() == before and restarts.count == 0


def test_default_settings_are_not_written(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, _ = setup
    editor.save_service_settings(
        {
            "network": {"enabled": True, "codec": "jpeg", "quality": "85"},
            "port": editor.service.config.port,
            "slots": "4",
        }
    )
    written = saved(file)
    assert written["network"] is True and "slots" not in written and "ui" not in written


def test_yaml_editor_round_trip(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, restarts = setup
    editor.save_camera({"name": "a", **synthetic()})
    text = editor.yaml_text()
    assert "cameras:" in text
    result = editor.save_yaml(text.replace("synthetic://64x48@30", "synthetic://32x16@30"))
    assert result == {"ok": True, "restart": False}
    assert saved(file)["cameras"]["a"]["source"] == "synthetic://32x16@30"
    assert editor.service.config.streams["a"].source == "synthetic://32x16@30"  # applied live
    assert restarts.count == 0


def test_yaml_editor_restarts_for_service_settings(
    setup: tuple[Editor, ConfigFile, Restarts],
) -> None:
    editor, _, restarts = setup
    assert editor.save_yaml(editor.yaml_text() + "slots: 6\n")["restart"] is True
    assert restarts.count == 1


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("cameras: [unclosed", "isn't valid YAML on line 1"),
        ("- just a list", "should start with keys"),
        ("cameras:\n  a:\n    source: synthetic://64x48\n    fps: -1\n", "greater than 0"),
        ("cameras:\n  a:\n    colour: red\n", "colour"),
    ],
)
def test_yaml_editor_errors(
    setup: tuple[Editor, ConfigFile, Restarts], text: str, message: str
) -> None:
    editor, file, _ = setup
    before = file.path.read_text()
    with pytest.raises(EditError, match=message):
        editor.save_yaml(text)
    assert file.path.read_text() == before  # nothing saved


def test_yaml_editor_never_shows_passwords(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, _ = setup
    editor.save_camera(
        {
            "name": "cam",
            "stream_port": stream_port(),
            "kind": "camera",
            "ip": "127.0.0.1",
            "port": 1,
            "brand": "hikvision",
            "password": "s3cret",
        }
    )
    editor.save_camera(
        {
            "name": "url",
            "stream_port": stream_port(),
            "kind": "rtsp",
            "source": "rtsp://127.0.0.1:1/x",
            "username": "u",
            "password": "s3cret",
        }
    )
    text = editor.yaml_text()
    assert "s3cret" not in text and text.count(MASK) == 2
    editor.save_yaml(text.replace("brand: hikvision", "brand: dahua"))
    cameras = saved(file)["cameras"]
    assert cameras["cam"]["password"] == "s3cret" and cameras["cam"]["brand"] == "dahua"
    assert cameras["url"]["source"] == "rtsp://u:s3cret@127.0.0.1:1/x"


def test_mask_keeps_environment_references() -> None:
    doc = {
        "cameras": {"a": {"ip": "x", "password": "${PASS}", "source": "rtsp://u:${P}@h/x"}},
        "ui": {"password": "pw"},
    }
    masked = _mask(doc)
    assert masked["cameras"]["a"]["password"] == "${PASS}"  # not a secret: shown as written
    assert masked["ui"]["password"] == MASK
    assert _unmask(masked, doc) == doc


def test_mask_hides_the_camera_links_password() -> None:
    doc = {"ui": {"links": "network", "links_password": "view"}}
    masked = _mask(doc)
    assert masked["ui"]["links_password"] == MASK
    assert _unmask(masked, doc) == doc
    with pytest.raises(EditError, match="Type the camera links password again"):
        _unmask(masked, {})


def test_unmask_asks_again_for_renamed_cameras() -> None:
    old = {"cameras": {"a": {"ip": "x", "brand": "axis", "password": "pw"}}}
    renamed = {"cameras": {"b": {"ip": "x", "brand": "axis", "password": MASK}}}
    with pytest.raises(EditError, match="Type the password for 'b' again"):
        _unmask(renamed, old)
    moved = {"cameras": {"a": {"source": f"rtsp://u:{MASK}@other-host/x"}}}
    with pytest.raises(EditError, match="Type the password in rtsp://other-host/x again"):
        _unmask(moved, {"cameras": {"a": {"source": "rtsp://u:pw@host/x"}}})


def test_without_restart_support_saves_and_says_so(
    setup: tuple[Editor, ConfigFile, Restarts],
) -> None:
    editor, file, _ = setup
    plain = Editor(editor.service, file, restart=None)
    result = plain.save_service_settings({"port": editor.service.config.port, "slots": "6"})
    assert result == {"ok": True, "restart": False, "note": "Saved. Restart faicam to apply."}
    assert saved(file)["slots"] == 6


def test_pause_and_resume(setup: tuple[Editor, ConfigFile, Restarts]) -> None:
    editor, file, restarts = setup
    editor.save_camera({"name": "a", **synthetic()})
    assert editor.set_paused("a", True) == {"ok": True, "restart": False}  # applied live
    assert saved(file)["cameras"]["a"]["paused"] is True
    assert editor.service.config.streams["a"].paused and editor.camera_settings("a")["paused"]
    status = {s["name"]: s for s in editor.service.describe()}
    assert status["a"]["state"] == "paused"
    # editing a paused camera keeps it paused
    editor.save_camera({"name": "a", "original": "a", **SYNTHETIC, "fps": "5"})
    assert saved(file)["cameras"]["a"]["paused"] is True
    editor.set_paused("a", False)
    assert "paused" not in saved(file)["cameras"]["a"] and restarts.count == 0
    with pytest.raises(EditError, match="No camera named 'ghost'"):
        editor.set_paused("ghost", True)


def test_link_addresses() -> None:
    assert "localhost" in links_url(UIConfig())  # nothing shared
    shared = UIConfig(links="network")
    assert "localhost" not in links_url(shared)  # the address other devices use
    assert "localhost" in page_url(shared)  # the page itself stays on this computer
    assert (
        links_url(UIConfig(links="network", bind="10.1.2.3", port=7390)) == "http://10.1.2.3:7390"
    )
