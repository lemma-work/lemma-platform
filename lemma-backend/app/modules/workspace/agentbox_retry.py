"""Shared retry primitives for transient AgentBox manager/app errors.

The in-sandbox apps (runtime, function_executor) start asynchronously after the
sandbox VM reports RUNNING, so the manager proxy can briefly return retryable
5xx / connection-refused responses while an app is still binding its port. These
helpers retry such transient failures with bounded exponential backoff. Genuine
4xx responses and non-transport exceptions are never retried — they propagate
immediately so real errors (bad request, function failure) surface at once.

Extracted from ``agentbox_session.py`` so both the agent session path and the
function-executor path share one definition.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Awaitable, Callable, TypeVar

import httpx

_MAX_AGENTBOX_ERROR_MESSAGE_LENGTH = 500

RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({500, 502, 503, 504})
RETRYABLE_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
    OSError,
)

# A subset of the above that provably occur *before* the request is delivered to
# the upstream app: the connection was never established, so the request could
# not have run. These are the only transport errors safe to retry for a
# NON-IDEMPOTENT call (e.g. a synchronous function execute that has a side
# effect). The errors omitted here -- ReadError/ReadTimeout/RemoteProtocolError/
# WriteError/WriteTimeout/OSError -- can all fire *after* the request reached and
# ran in the app (the failure is only on the response leg), so re-sending would
# run the side effect a second time. This mirrors why sync executes also drop
# 504 from ``RETRYABLE_HTTP_STATUS_CODES``.
CONNECT_PHASE_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
)

DEFAULT_MAX_ATTEMPTS = 12
DEFAULT_INITIAL_RETRY_DELAY_SECONDS = 0.25
DEFAULT_MAX_RETRY_DELAY_SECONDS = 2.0

T = TypeVar("T")


def truncate_message(
    message: str, limit: int = _MAX_AGENTBOX_ERROR_MESSAGE_LENGTH
) -> str:
    if len(message) <= limit:
        return message
    return f"{message[:limit]}... [truncated]"


def format_http_status_error(exc: httpx.HTTPStatusError) -> str:
    status_code = exc.response.status_code
    url = str(exc.request.url)
    reason = exc.response.reason_phrase or "HTTP error"
    detail = ""
    with contextlib.suppress(Exception):
        detail = exc.response.text.strip()
    if detail:
        return truncate_message(
            f"AgentBox request failed with HTTP {status_code} {reason} at {url}: {detail}"
        )
    return truncate_message(
        f"AgentBox request failed with HTTP {status_code} {reason} at {url}."
    )


def is_retryable_http_error(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code in RETRYABLE_HTTP_STATUS_CODES


def format_transport_error(exc: BaseException) -> str:
    return truncate_message(f"{type(exc).__name__}: {exc}")


async def retry_on_transient_agentbox_error(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay: float = DEFAULT_INITIAL_RETRY_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_RETRY_DELAY_SECONDS,
    on_retry: Callable[[int, str], None] | None = None,
    retryable_status_codes: frozenset[int] = RETRYABLE_HTTP_STATUS_CODES,
    retryable_transport_errors: tuple[type[BaseException], ...] = RETRYABLE_TRANSPORT_ERRORS,
) -> T:
    """Run ``operation`` retrying only transient transport / retryable-5xx errors.

    A non-retryable ``HTTPStatusError`` (e.g. 4xx) and any non-transport
    exception propagate immediately. The last transient error is re-raised once
    ``max_attempts`` is exhausted. ``on_retry(attempt, message)`` is a logging
    hook invoked before each backoff sleep. ``retryable_status_codes`` narrows
    which 5xx codes are retried -- e.g. a non-idempotent synchronous call drops
    504 (gateway timeout) so a request that already reached the app is not
    re-sent. ``retryable_transport_errors`` narrows which transport exceptions
    are retried for the same reason -- a non-idempotent call passes
    ``CONNECT_PHASE_TRANSPORT_ERRORS`` so a request that may have already run in
    the app (a response-leg failure) is not re-sent and its side effect not
    duplicated.
    """
    delay = initial_delay
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code not in retryable_status_codes
                or attempt == max_attempts
            ):
                raise
            error = format_http_status_error(exc)
        except retryable_transport_errors as exc:
            if attempt == max_attempts:
                raise
            error = format_transport_error(exc)
        if on_retry is not None:
            on_retry(attempt, error)
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, max_delay)
    # Unreachable: the final attempt either returns or re-raises above.
    raise RuntimeError("retry_on_transient_agentbox_error exhausted without result")
