"""Centralized redaction for logs, errors, traces, and diagnostic payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "token",
        "secret",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "client_secret",
        "access_key",
        "private_key",
        "credential",
    }
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|cookie|token|secret|password|passwd|api[_-]?key|"
    r"client[_-]?secret|access[_-]?key|private[_-]?key|credential)"
    r"(\s*[:=]\s*)[^\s,;]+"
)
_SENSITIVE_URL_PARAMS = frozenset(
    {"code", "state", "signature", "sig", "key", "oauth_verifier"}
)


def is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part.replace("-", "_") in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value

    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    netloc = f"{REDACTED}@{hostname}" if parsed.username or parsed.password else hostname
    query = urlencode(
        [
            (
                key,
                REDACTED
                if is_sensitive_key(key) or key.lower() in _SENSITIVE_URL_PARAMS
                else item,
            )
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def redact_text(value: str) -> str:
    redacted = _BEARER_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", value)
    redacted = _JWT_RE.sub(REDACTED, redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", redacted
    )
    redacted = _URL_RE.sub(lambda match: _redact_url(match.group(0)), redacted)
    return redacted


def redact_value(value: Any, *, key: object | None = None) -> Any:
    """Return a JSON/log-safe copy with secrets removed recursively."""
    if key is not None and is_sensitive_key(key):
        return REDACTED
    if isinstance(value, BaseException):
        return {"type": type(value).__name__}
    if isinstance(value, Mapping):
        return {
            item_key: redact_value(item, key=item_key)
            for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


def redact_event_dict(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact_value(event_dict)
