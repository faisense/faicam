"""faicam's command: it runs faistream and the page, and restarts itself for settings."""

from pathlib import Path


def test_settings_change_restarts_in_place(tmp_path: Path) -> None:
    """Service-wide settings apply by faicam replacing itself: same process, new settings."""
    import json
    import subprocess
    import sys
    import time
    import urllib.request

    from tests.helpers import free_port_block

    port, ui_port = free_port_block(), free_port_block(1)
    (tmp_path / "cameras.yaml").write_text(
        f"port: {port}\nui:\n  port: {ui_port}\ncameras:\n  cam:\n    source: synthetic://64x48@30\n"
        f"    stream_port: {port + 1}\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "faicam"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{ui_port}"

    def state() -> dict[str, object] | None:
        try:
            with urllib.request.urlopen(base + "/api/state", timeout=2) as response:
                return json.loads(response.read())  # type: ignore[no-any-return]
        except OSError:
            return None

    def wait_for(check: object, seconds: float = 20) -> dict[str, object]:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            current = state()
            if current is not None and check(current):  # type: ignore[operator]
                return current
            time.sleep(0.2)
        raise AssertionError("faicam didn't come back")

    try:
        wait_for(lambda s: True)
        body = json.dumps({"port": port, "slots": 6, "ui": {"port": ui_port}}).encode()
        request = urllib.request.Request(
            base + "/api/settings", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert json.loads(response.read())["restart"] is True
        current = wait_for(lambda s: s["service"]["slots"] == 6)
        assert current["cameras"][0]["name"] == "cam"  # type: ignore[index]
        assert proc.poll() is None  # still the same process
    finally:
        proc.terminate()
        output = proc.communicate(timeout=15)[0]
    assert "Restarting faicam" in output
    assert output.count("faicam is running") == 2
