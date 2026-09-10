"""Retry only provider dispatches, after accounting for each failed attempt."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math

import httpx
import httpx2
from anthropic import APIConnectionError as AnthropicConnectionError
from openai import APIConnectionError as OpenAIConnectionError
from pydantic_ai.exceptions import ModelHTTPError

from app.core.domain.errors import DomainError

MAX_PROVIDER_ATTEMPTS = 3
PROVIDER_ERRORS = (
    ModelHTTPError,
    httpx.TransportError,
    httpx.HTTPStatusError,
    httpx2.TransportError,
    httpx2.HTTPStatusError,
    OpenAIConnectionError,
    AnthropicConnectionError,
    TimeoutError,
)
_RETRYABLE_STATUSES = frozenset({408, 409, 429})

#: The transport-level drops the *harness* owns, not this layer.
#:
#: A connection that dies while a stream is being opened is the same failure as
#: one that dies mid-answer, and the harness already handles it: it retries from
#: the messages it has recorded, and emits a ``stream_reset`` token so the client
#: discards the half-answer it was shown. Retrying underneath that is invisible
#: to it -- the reset never fires, and once the attempts run out the original
#: error is replaced by ``ProviderAttemptsExhaustedError``, so the specific
#: "the connection kept dropping" message becomes unreachable and the reader is
#: told to try again later with no idea what happened.
#:
#: ``ModelHTTPError`` is deliberately absent: the provider answered, there is no
#: partial stream to reset, and retrying it here is what buys the ``Retry-After``
#: handling above.
_HARNESS_OWNED_DROPS = (
    httpx.TransportError,
    httpx2.TransportError,
    OpenAIConnectionError,
    AnthropicConnectionError,
    TimeoutError,
)


def is_harness_owned_drop(exc: BaseException) -> bool:
    """Whether the harness's own stream retry should see this, unretried."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ModelHTTPError):
            return False
        if isinstance(current, _HARNESS_OWNED_DROPS):
            return True
        current = current.__cause__
    return False


def retry_delay(exc: Exception, attempt: int) -> float | None:
    """Return bounded backoff for a transient provider failure, otherwise None."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, DomainError):
            return None
        if isinstance(current, ModelHTTPError):
            if (
                current.status_code not in _RETRYABLE_STATUSES
                and current.status_code < 500
            ):
                return None
            seconds = current.retry_after
            if seconds is not None and math.isfinite(seconds):
                return min(60.0, max(0.0, seconds))
            return min(30.0, 2.0**attempt)
        if isinstance(current, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
            if (
                current.response.status_code not in _RETRYABLE_STATUSES
                and current.response.status_code < 500
            ):
                return None
            return _header_delay(current.response.headers.get("retry-after"), attempt)
        if isinstance(
            current, (httpx.TransportError, httpx2.TransportError, TimeoutError)
        ):
            return min(30.0, 2.0**attempt)
        current = current.__cause__
    return None


def _header_delay(value: str | None, attempt: int) -> float:
    if value is not None:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds()
            except TypeError, ValueError, OverflowError:
                seconds = float("nan")
        if math.isfinite(seconds):
            return min(60.0, max(0.0, seconds))
    return min(30.0, 2.0**attempt)


def confirmed_rejection(exc: Exception) -> bool:
    """A validation/authentication/rate-limit response rejected this dispatch."""
    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
    elif isinstance(exc, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
        status = exc.response.status_code
    else:
        return False
    # Timeouts and server errors cannot establish whether work already ran.
    return status in {400, 401, 402, 403, 404, 409, 422, 429}
