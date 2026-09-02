from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class LemmaError(Exception):
    """Base exception for the Lemma SDK."""


class LemmaConfigError(LemmaError):
    """Raised when SDK configuration is missing or invalid."""


@dataclass
class LemmaAPIError(LemmaError):
    status_code: int
    message: str
    code: str | None = None
    details: Any | None = None
    raw_response: Any | None = None
    request_id: str | None = None

    def __str__(self) -> str:
        prefix = f"[{self.status_code}]"
        code = f" {self.code}:" if self.code else ""
        suffix = f" (request_id={self.request_id})" if self.request_id else ""
        return f"{prefix}{code} {self.message}{suffix}"


# Typed subclasses so callers can `except LemmaNotFoundError` / `except
# LemmaRateLimitError` instead of switching on status_code. Every one is still a
# LemmaAPIError, so existing `except LemmaAPIError` handlers keep working.
class LemmaAuthError(LemmaAPIError):
    """401 — session missing or expired."""


class LemmaPermissionError(LemmaAPIError):
    """403 — authenticated but not permitted (often an RLS/grant denial)."""


class LemmaNotFoundError(LemmaAPIError):
    """404 — resource not found."""


class LemmaConflictError(LemmaAPIError):
    """409 — conflict (e.g. duplicate name)."""


@dataclass
class LemmaRateLimitError(LemmaAPIError):
    """429 — rate limited. ``retry_after`` is the server-advised wait in seconds.

    A real dataclass field (not a bare class attribute) so it participates in the
    generated ``__init__``/``__repr__``/``__eq__`` like the inherited fields."""

    retry_after: float | None = None


class LemmaServerError(LemmaAPIError):
    """5xx — server-side error."""


class LemmaConnectionError(LemmaError):
    """Transport-level failure (connection refused, DNS, reset) — no HTTP status."""


class LemmaTimeoutError(LemmaConnectionError):
    """Request exceeded the configured timeout."""


_STATUS_ERRORS: dict[int, type[LemmaAPIError]] = {
    401: LemmaAuthError,
    403: LemmaPermissionError,
    404: LemmaNotFoundError,
    409: LemmaConflictError,
    429: LemmaRateLimitError,
}


def api_error(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    details: Any | None = None,
    raw_response: Any | None = None,
    retry_after: float | None = None,
    request_id: str | None = None,
) -> LemmaAPIError:
    """Build the most specific LemmaAPIError subclass for an HTTP status."""
    cls = _STATUS_ERRORS.get(status_code)
    if cls is None:
        cls = LemmaServerError if status_code >= 500 else LemmaAPIError
    fields: dict[str, Any] = {
        "status_code": status_code,
        "message": message,
        "code": code,
        "details": details,
        "raw_response": raw_response,
        "request_id": request_id,
    }
    # Only LemmaRateLimitError carries the extra dataclass field; the other
    # subclasses inherit the base __init__ and don't accept retry_after.
    if cls is LemmaRateLimitError:
        return cls(**fields, retry_after=retry_after)
    return cls(**fields)
