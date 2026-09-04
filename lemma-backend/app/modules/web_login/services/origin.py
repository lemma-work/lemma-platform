"""Turning what somebody typed into the thing a session belongs to.

A saved login belongs to an *origin* — scheme and host — not to a URL. The page a
login happens to start at is incidental: `example.com/login`, `example.com/signin`
and `example.com/account` are one credential, and storing three of them is three
chances to pick the wrong one.
"""

from __future__ import annotations

from urllib.parse import urlparse


class InvalidOrigin(ValueError):
    """Not something a session can belong to."""


def normalize_origin(value: str) -> str:
    """Scheme and host, lower-cased, with the path dropped.

    A bare host is read as https, because that is what somebody means when they
    type one and the alternative is silently saving a session against a plaintext
    origin they never asked for.
    """
    candidate = (value or "").strip()
    if not candidate:
        raise InvalidOrigin("An origin is required")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidOrigin(f"Unsupported scheme: {parsed.scheme or 'none'}")
    if not parsed.hostname:
        raise InvalidOrigin("An origin needs a host")

    host = parsed.hostname.lower()
    if parsed.port and not _is_default_port(parsed.scheme, parsed.port):
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
