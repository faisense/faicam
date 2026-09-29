import numpy as np
import pytest

from faicam.brands import BRANDS, DetectError, detect, normalize_brand, rtsp_url

FRAME = np.zeros((720, 1280, 3), np.uint8)


def test_rtsp_url_escapes_credentials() -> None:
    url = rtsp_url("10.0.0.5", "/Streaming/Channels/101", "admin", "p@ss:w/rd#1")
    assert url == "rtsp://admin:p%40ss%3Aw%2Frd%231@10.0.0.5:554/Streaming/Channels/101"


def test_rtsp_url_variants() -> None:
    assert rtsp_url("10.0.0.5", "stream1") == "rtsp://10.0.0.5:554/stream1"
    assert rtsp_url("10.0.0.5", "/s", "admin", port=8554) == "rtsp://admin@10.0.0.5:8554/s"
    assert rtsp_url("fe80::1", "/s") == "rtsp://[fe80::1]:554/s"
    assert rtsp_url("10.0.0.5", "/s", password="pw") == "rtsp://:pw@10.0.0.5:554/s"


def test_brand_aliases() -> None:
    assert normalize_brand("Wisenet") == "hanwha"
    assert normalize_brand(" HIKVISION ") == "hikvision"


def fake_camera(working_path: str):  # type: ignore[no-untyped-def]
    tried: list[str] = []

    def probe(url: str, timeout: float) -> np.ndarray:
        tried.append(url)
        if url.endswith(working_path):
            return FRAME
        raise RuntimeError("404")

    return probe, tried


def test_detect_finds_the_brand() -> None:
    probe, tried = fake_camera(BRANDS["reolink"].path)
    found = detect("10.0.0.5", "admin", "pw", probe=probe, check_reachable=lambda *_: True)
    assert (found.brand, found.path, found.width, found.height) == (
        "reolink",
        "/h264Preview_01_main",
        1280,
        720,
    )
    assert tried[0].endswith(BRANDS["hikvision"].path)  # most common brands first


def test_detect_with_known_brand_tries_only_that_path() -> None:
    probe, tried = fake_camera(BRANDS["hanwha"].path)
    found = detect("10.0.0.5", brand="wisenet", probe=probe, check_reachable=lambda *_: True)
    assert found.brand == "hanwha" and len(tried) == 1


def test_detect_with_custom_path() -> None:
    probe, _ = fake_camera("/custom/live")
    found = detect("10.0.0.5", path="/custom/live", probe=probe, check_reachable=lambda *_: True)
    assert found.path == "/custom/live" and found.brand is None


def test_detect_unreachable_camera() -> None:
    with pytest.raises(DetectError, match=r"Can't reach 10\.0\.0\.5 on port 554"):
        detect("10.0.0.5", check_reachable=lambda *_: False)


def test_detect_no_stream() -> None:
    probe, _ = fake_camera("/nothing-matches")
    with pytest.raises(DetectError, match="username and password"):
        detect("10.0.0.5", probe=probe, check_reachable=lambda *_: True)


def test_detect_real_port_check() -> None:
    # Nothing listens on port 1: fails fast with the "can't reach" message.
    with pytest.raises(DetectError, match="Can't reach"):
        detect("127.0.0.1", port=1)
