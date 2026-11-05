"""Retry only provider dispatches, after accounting for each failed attempt."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import sys

import httpx
import httpx2
from pydantic_ai.exceptions import ModelHTTPError

from app.core.domain.errors import DomainError

MAX_PROVIDER_ATTEMPTS = 3

# Every check names both HTTP stacks. The provider SDKs run on `httpx2`, other
# callers still raise `httpx`, and neither hierarchy subclasses the other, so a
# check that names one silently misses the other's failures.
_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    httpx.TransportError,
    httpx2.TransportError,
)
_STATUS_ERRORS = (httpx.HTTPStatusError, httpx2.HTTPStatusError)

#: Each provider SDK's connection error, by the package that exports it.
#:
#: Named, not imported. Importing ``anthropic`` to name its error loaded that
#: whole SDK -- thousands of modules -- into every process that metered a model
#: call, on the event loop of the first turn to do it, whether or not anything
#: in the process talks to Anthropic. ``runtime_model_factory`` imports the SDK
#: only to build an Anthropic client, and this module now agrees with it.
_SDK_CONNECTION_ERRORS = (
    ("openai", "APIConnectionError"),
    ("anthropic", "APIConnectionError"),
)
_RETRYABLE_STATUSES = frozenset({408, 409, 429})


def _sdk_connection_errors() -> tuple[type[Exception], ...]:
    """The connection errors of the provider SDKs this process has imported.

    An SDK's error can only be raised once something has imported the SDK, so
    one missing from ``sys.modules`` has no error in flight to recognise. Read
    at each check rather than once, so an SDK imported later still counts.
    """
    errors: list[type[Exception]] = []
    for package, name in _SDK_CONNECTION_ERRORS:
        error = getattr(sys.modules.get(package), name, None)
        if isinstance(error, type) and issubclass(error, Exception):
            errors.append(error)
    return tuple(errors)


def provider_errors() -> tuple[type[Exception], ...]:
    """What a provider dispatch fails with, for ``except provider_errors():``.

    An ``except`` expression is evaluated after the raise, by which time the
    call that raised has imported its SDK.
    """
    return (
        ModelHTTPError,
        *_TRANSPORT_ERRORS,
        *_STATUS_ERRORS,
        *_sdk_connection_errors(),
        TimeoutError,
    )


def _harness_owned_drops() -> tuple[type[Exception], ...]:
    """The transport-level drops the *harness* owns, not this layer.

    A connection that dies while a stream is being opened is the same failure as
    one that dies mid-answer, and the harness already handles it: it retries from
    the messages it has recorded, and emits a ``stream_reset`` token so the
    client discards the half-answer it was shown. Retrying underneath that is
    invisible to it -- the reset never fires, and once the attempts run out the
    original error is replaced by ``ProviderAttemptsExhaustedError``, so the
    specific "the connection kept dropping" message becomes unreachable and the
    reader is told to try again later with no idea what happened.

    ``ModelHTTPError`` is deliberately absent: the provider answered, there is
    no partial stream to reset, and retrying it here is what buys the
    ``Retry-After`` handling below.
    """
    return (*_TRANSPORT_ERRORS, *_sdk_connection_errors(), TimeoutError)


def is_harness_owned_drop(exc: BaseException) -> bool:
    """Whether the harness's own stream retry should see this, unretried."""
    harness_owned = _harness_owned_drops()
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ModelHTTPError):
            return False
        if isinstance(current, harness_owned):
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
        if isinstance(current, _STATUS_ERRORS):
            if (
                current.response.status_code not in _RETRYABLE_STATUSES
                and current.response.status_code < 500
            ):
                return None
            return _header_delay(current.response.headers.get("retry-after"), attempt)
        if isinstance(current, (*_TRANSPORT_ERRORS, TimeoutError)):
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
    elif isinstance(exc, _STATUS_ERRORS):
        status = exc.response.status_code
    else:
        return False
    # Timeouts and server errors cannot establish whether work already ran.
    return status in {400, 401, 402, 403, 404, 409, 422, 429}
