"""Which redirect URIs a client may send a person's browser to.

The consent page navigates the browser to whatever redirect URI the request
ends with, on the auth site, with the person signed in. A URI that runs code
there -- ``javascript:``, ``data:`` and their relatives -- would run it with the
person's session. So every redirect URI is checked here, on every path that can
produce one: registration, a metadata document's list, an exact match, and the
single-URI default. A match against what the client registered is not enough
on its own; a hosted metadata document can register anything.

Allowed: ``https``; ``http`` to a loopback host only (RFC 8252 §7.3); and an
app's own scheme (``cursor://``, ``com.example.app:``), which the operating
system hands to that app rather than to a page.
"""

from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import urlsplit

_REFUSED_SCHEMES = frozenset(
    {
        "javascript",
        "vbscript",
        "data",
        "blob",
        "file",
        "filesystem",
        "about",
        "view-source",
        "jar",
        "ws",
        "wss",
        "ftp",
    }
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*$")


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def redirect_allowed(uri: str) -> bool:
    try:
        parts = urlsplit(uri.strip())
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not _SCHEME.match(scheme) or scheme in _REFUSED_SCHEMES:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if any(ord(char) < 0x21 for char in uri):
        # Control characters and spaces: a browser strips or reinterprets
        # them, so the URI it follows is not the one checked here.
        return False
    if scheme == "https":
        return bool(parts.hostname)
    if scheme == "http":
        return _is_loopback(parts.hostname)
    return True
