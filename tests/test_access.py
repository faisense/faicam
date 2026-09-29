import pytest

from faicam.access import check, host_name, is_direct, is_loopback
from faicam.config import UIConfig

HERE, PHONE = "127.0.0.1", "192.168.1.30"
URL = "http://localhost:7380"


def ask(
    ui: UIConfig,
    kind: str,
    client: str = HERE,
    host: str = "localhost",
    password: str | None = None,
) -> int:
    refusal = check(ui, kind, client=client, host=host, password=password, page_url=URL)  # type: ignore[arg-type]
    return 200 if refusal is None else refusal.status


def test_everything_stays_on_this_computer_by_default() -> None:
    ui = UIConfig()
    assert ask(ui, "page") == 200 and ask(ui, "link") == 200
    assert ask(ui, "page", PHONE, "192.168.1.50") == 403
    assert ask(ui, "link", PHONE, "192.168.1.50") == 403
    assert ask(ui, "page", host="evil.example") == 403  # DNS rebinding


def test_links_can_be_shared_without_the_page() -> None:
    ui = UIConfig(links="network")
    assert ask(ui, "link", PHONE, "192.168.1.50") == 200
    assert ask(ui, "page", PHONE, "192.168.1.50") == 403
    assert ask(ui, "link", PHONE, "evil.example") == 403  # DNS rebinding


def test_links_password() -> None:
    ui = UIConfig(links="network", links_password="view")
    assert ask(ui, "link", PHONE, "192.168.1.50") == 401
    assert ask(ui, "link", PHONE, "192.168.1.50", "view") == 200
    assert ask(ui, "link", PHONE, "any.name", "view") == 200  # the password protects it
    assert ask(ui, "link") == 200  # this computer needs none


def test_a_shared_page_needs_its_password_and_opens_links() -> None:
    ui = UIConfig(page="network", password="admin")
    assert ask(ui, "page", PHONE, "192.168.1.50") == 401
    assert ask(ui, "page", PHONE, "192.168.1.50", "admin") == 200
    assert ask(ui, "link", PHONE, "192.168.1.50") == 401  # asks for the page password
    assert ask(ui, "link", PHONE, "192.168.1.50", "admin") == 200
    refusal = check(ui, "link", client=PHONE, host="x", password=None, page_url=URL)
    assert refusal is not None and refusal.realm == "faicam"


@pytest.mark.parametrize(
    ("header", "name"),
    [("localhost:7380", "localhost"), ("[::1]:7380", "::1"), ("10.0.0.5", "10.0.0.5"), ("", "")],
)
def test_host_name(header: str, name: str) -> None:
    assert host_name(header) == name


def test_addresses() -> None:
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("::ffff:127.0.0.1")
    assert not is_loopback("192.168.1.5") and not is_loopback("nonsense")
    assert is_direct("192.168.1.5") and is_direct("fe80::1") and is_direct("localhost")
    assert not is_direct("evil.example") and not is_direct("")
