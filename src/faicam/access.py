"""Who may open what on the web server.

There are two kinds of address, each with its own setting:

- ``page``: the page itself and its API, where cameras are added, edited and paused
  (``ui.page``; opening it to other machines needs ``ui.password``);
- ``link``: a camera's links, ``/live/<name>.mjpg`` and ``/snapshot/<name>.jpg``, which
  only show video (``ui.links``, with an optional ``ui.links_password``).

So phones, TVs and other computers can watch the cameras without being able to change
anything. Whoever knows the page password can open every link too.
"""

from __future__ import annotations

import hmac
import ipaddress
import socket
from typing import Literal, NamedTuple

from faicam.config import LOOPBACK, UIConfig

Kind = Literal["page", "link"]
PAGE_REALM = "faicam"
LINKS_REALM = "faicam camera links"


class Refusal(NamedTuple):
    status: int  # 401 asks for a password, 403 can't be allowed
    message: str  # for people
    realm: str = PAGE_REALM  # which password a 401 asks for


def check(
    ui: UIConfig,
    kind: Kind,
    *,
    client: str,
    host: str,
    password: str | None,
    page_url: str,
) -> Refusal | None:
    """Why a request can't go ahead, or None if it may.

    ``client`` is the IP address the request came from, ``host`` the Host header's
    name (without the port) and ``password`` the one sent with HTTP Basic auth.
    """
    local = is_loopback(client)
    if kind == "page":
        if ui.page == "network":
            if _matches(password, ui.password):
                return None
            return Refusal(401, "Enter the faicam password.")
        if not local:
            return Refusal(403, "This page only opens on the computer running faicam.")
        if host not in LOOPBACK:  # a website pointing its own name at 127.0.0.1
            return Refusal(403, f"Open the page at {page_url}")
        return None

    if ui.page == "network" and _matches(password, ui.password):
        return None
    if local or ui.links == "network":
        if ui.links_password and not local:
            if not _matches(password, ui.links_password):
                return Refusal(401, "Enter the password for the camera links.", LINKS_REALM)
        elif not is_direct(host):
            return Refusal(403, "Open camera links with this computer's IP address or name.")
        return None
    if ui.page == "network":
        return Refusal(401, "Enter the faicam password.")
    return Refusal(
        403,
        "Camera links only open on the computer running faicam. To watch on other "
        "devices, share the camera links in Settings on that computer.",
    )


def host_name(header: str) -> str:
    """The name in a Host header, without the port: ``[::1]:7380`` gives ``::1``."""
    if header.startswith("["):
        return header[1:].split("]", 1)[0]
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def is_loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def is_direct(host: str) -> bool:
    """An IP address, localhost or this computer's own name: never a website's domain.

    A website can point its own name at a device on the visitor's network and read
    the answers (DNS rebinding); those requests carry the website's name here.
    """
    if host in LOOPBACK:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    own = socket.gethostname().lower().removesuffix(".local")
    return bool(host) and host.lower().removesuffix(".local") == own


def _matches(given: str | None, expected: str | None) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())
