"""Centralized redaction for logs, errors, traces, and diagnostic payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "[REDACTED]"
REDACTED_URL = "[REDACTED_URL]"
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
    r"""(?ix)
    (?<![a-z0-9_])
    (?P<quote>["']?)
    (?P<key>
        authorization|cookie|token|secret|password|passwd|api[_-]?key|
        client[_-]?secret|access[_-]?key|private[_-]?key|credential
    )
    (?P=quote)
    (?P<separator>\s*[:=]\s*)
    (?:"[^"]*"|'[^']*'|[^\s,;]+)
    """
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_KNOWN_TOKEN_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # Telegram Bot API tokens appear in the request path rather than an
    # Authorization header, so generic URL query redaction cannot catch them.
    re.compile(r"\b[0-9]{6,}:[A-Za-z0-9_-]{20,}\b"),
)
_SENSITIVE_URL_PARAMS = frozenset(
    {
        "client_assertion",
        "code",
        "key",
        "oauth_verifier",
        "sig",
        "signature",
        "state",
        "x-amz-credential",
        "x-amz-signature",
        "x-goog-signature",
    }
)


def is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part.replace("-", "_") in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.netloc:
            return value

        # Accessing hostname/port performs additional validation in urllib and
        # can raise even when urlsplit itself succeeded (for example ``:bad``
        # ports or malformed IPv6 literals). Keep the whole operation inside
        # the fail-safe boundary: redaction must never affect application flow.
        hostname = parsed.hostname or ""
        port = parsed.port
        if port is not None:
            hostname = f"{hostname}:{port}"
        netloc = (
            f"{REDACTED}@{hostname}" if parsed.username or parsed.password else hostname
        )
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
        fragment_items = parse_qsl(parsed.fragment, keep_blank_values=True)
        fragment = parsed.fragment
        if any(
            is_sensitive_key(key) or key.lower() in _SENSITIVE_URL_PARAMS
            for key, _ in fragment_items
        ):
            fragment = REDACTED
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
    except Exception:
        # Never return a malformed URL-like input: it can contain credentials,
        # query secrets, or parser edge cases that the normal rules did not see.
        return REDACTED_URL


def redact_text(value: str) -> str:
    redacted = _PRIVATE_KEY_RE.sub(REDACTED, value)
    redacted = _BEARER_RE.sub(
        lambda match: f"{match.group(1)} {REDACTED}",
        redacted,
    )
    redacted = _JWT_RE.sub(REDACTED, redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}{REDACTED}",
        redacted,
    )
    for pattern in _KNOWN_TOKEN_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
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
    try:
        redacted = redact_value(event_dict)
        return redacted if isinstance(redacted, dict) else {}
    except Exception:
        # Preserve only processor metadata needed by the downstream logging
        # contract. Do not expose the value or exception that defeated
        # redaction, and never allow a log call to change application behavior.
        safe_keys = {
            "_lemma_app_owned",
            "_record",
            "deployment.environment",
            "event",
            "level",
            "logger",
            "release.sha",
            "service.name",
            "service.version",
            "timestamp",
        }
        return {key: value for key, value in event_dict.items() if key in safe_keys}
