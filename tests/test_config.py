from pathlib import Path

import pytest
from faistream.config import ConfigError

from faicam.config import parse_config


def test_camera_fields_build_the_stream_address() -> None:
    config = parse_config(
        {
            "cameras": {
                "door": {
                    "ip": "10.0.0.5",
                    "stream_port": 7301,
                    "username": "admin",
                    "password": "p@ss",
                    "brand": "Wisenet",
                    "backup": "yard",
                },
                "yard": {
                    "ip": "10.0.0.6",
                    "stream_port": 7302,
                    "brand": "other",
                    "path": "/live",
                    "port": 8554,  # the camera's RTSP port, not the stream's
                },
            }
        }
    )
    door = config.streams["door"]
    assert door.brand == "hanwha"
    assert door.primary == "rtsp://admin:p%40ss@10.0.0.5:554/profile2/media.smp"
    assert door.sources == [door.primary, "yard"]  # faistream hands yard's frames over
    assert config.streams["yard"].primary == "rtsp://10.0.0.6:8554/live"


@pytest.mark.parametrize(
    ("camera", "message"),
    [
        ({"ip": "10.0.0.5"}, "set 'brand'"),
        ({"ip": "10.0.0.5", "brand": "acme"}, "unknown brand"),
        ({"ip": "10.0.0.5", "brand": "axis", "source": "x"}, "either 'ip'"),
        ({"brand": "axis"}, "either 'ip'"),
    ],
)
def test_camera_errors(camera: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"cameras": {"door": {"stream_port": 7301, **camera}}})


def test_ui_settings() -> None:
    assert parse_config({}).ui is not None
    assert parse_config({"ui": False}).ui is None
    with pytest.raises(ConfigError, match="password"):
        parse_config({"ui": {"host": "0.0.0.0"}})
    assert parse_config({"ui": {"host": "0.0.0.0", "password": "x"}}).ui is not None


def test_config_file_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import stat

    from faicam.config import ConfigFile

    monkeypatch.setenv("CAM_PASS", "secret")
    path = tmp_path / "cameras.yaml"
    path.write_text(
        "cameras:\n"
        "  door:\n    ip: 10.0.0.5\n    stream_port: 7301\n    password: ${CAM_PASS}\n"
        "    brand: hikvision\n"
        "  yard:\n    source: synthetic://64x48\n    stream_port: 7302\n    backup: door\n"
        "    open_timeout: 2\n"
    )
    file = ConfigFile.open(path)
    assert "secret" in str(file.config().streams["door"].primary)

    renamed = file.with_camera("front", file.cameras()["door"], replaces="door")
    assert list(renamed["cameras"]) == ["front", "yard"]
    assert renamed["cameras"]["yard"]["backup"] == "front"  # references follow the rename
    file.save(renamed)

    text = path.read_text()
    assert "${CAM_PASS}" in text and "secret" not in text  # env references survive a save
    assert "open_timeout: 2" in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    removed = ConfigFile.open(path).without_camera("front")
    assert removed["cameras"] == {
        "yard": {"source": "synthetic://64x48", "stream_port": 7302, "open_timeout": 2}
    }


def test_config_file_rejects_duplicates(tmp_path: Path) -> None:
    from faicam.config import ConfigFile

    file = ConfigFile.open(tmp_path / "new.yaml")
    assert file.cameras() == {}
    raw = file.with_camera("a", {"source": "synthetic://64x48"})
    with pytest.raises(ConfigError, match="already exists"):
        ConfigFile(file.path, raw).with_camera("a", {"source": "synthetic://64x48"})


def test_page_and_links_are_shared_separately() -> None:
    from faicam.config import UIConfig

    assert UIConfig().host == "127.0.0.1"  # nothing shared: only this computer
    assert UIConfig(links="network").host == "0.0.0.0"
    assert UIConfig(links="network", bind="192.168.1.50").host == "192.168.1.50"
    with pytest.raises(ValueError, match="password"):
        UIConfig(page="network")
    # older files set `host`
    legacy = UIConfig.model_validate({"host": "0.0.0.0", "password": "pw"})
    assert (legacy.page, legacy.links, legacy.host) == ("network", "local", "0.0.0.0")
    assert UIConfig.model_validate({"host": "127.0.0.1"}).page == "local"
    assert UIConfig.model_validate({"host": "10.0.0.2", "password": "pw"}).bind == "10.0.0.2"
