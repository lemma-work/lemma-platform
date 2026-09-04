"""Which model-request failures are worth another attempt.

A provider that drops the SSE stream mid-response used to kill the whole
conversation run: the exception unwound past the graph into the harness's
catch-all and the user got "something went wrong" with no way back. Those drops
are transient by nature — the request was accepted, the connection died — so the
run can be resumed from the messages already recorded.

The distinction that matters is *transient transport* versus *the provider told
us something*. A 400 means the request is wrong, a 402 means the account is out
of credit, a 404 means the model doesn't exist: retrying any of them just burns
the same failure three times and delays the error the user needs to see. Only
connection-level failures and the handful of status codes that explicitly mean
"try again" are retried.

Mirrors the spirit of ``tools.tool_errors.is_control_flow_exception``: a small,
explicit allowlist, biased towards *not* retrying when unsure.
"""

from __future__ import annotations

import asyncio

import httpx
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.modules.agent.tools.tool_errors import AgentInputRequired

__all__ = ["RETRYABLE_STATUS_CODES", "is_retryable_stream_error", "retry_after_seconds"]

# 408 request timeout, 409 conflict (some gateways use it for "retry"),
# 429 rate limited. Everything >=500 is treated as retryable separately.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})

# Never retried, whatever else they look like: these carry run control flow or a
# deliberate stop, and re-entering the graph would either duplicate a pause or
# fight the thing that asked us to stop.
_NEVER_RETRY: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
    AgentInputRequired,
    UsageLimitExceeded,
    UnexpectedModelBehavior,
)


def _provider_connection_error(exc: BaseException) -> bool:
    """True for the provider SDKs' own "couldn't reach the API" wrappers.

    Imported lazily and matched by name so this module doesn't hard-depend on
    both SDKs being installed, and so a provider we haven't special-cased still
    benefits when it wraps an httpx error (the ``__cause__`` walk below).
    """
    return type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}


def is_retryable_stream_error(exc: BaseException) -> bool:
    """Whether re-entering the run after ``exc`` is worth trying."""
    if isinstance(exc, _NEVER_RETRY):
        return False

    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        return status in RETRYABLE_STATUS_CODES or status >= 500

    # httpx.TransportError covers ReadError/WriteError/ConnectError/ReadTimeout/
    # PoolTimeout/RemoteProtocolError — the whole mid-stream drop family, which
    # is what the production ReadErrors were.
    if isinstance(exc, httpx.TransportError):
        return True

    if _provider_connection_error(exc):
        return True

    # A bare TimeoutError reaches us when the transport times out without an
    # httpx wrapper. Checked after CancelledError above, which subclasses
    # BaseException rather than TimeoutError but is easy to conflate.
    if isinstance(exc, TimeoutError):
        return True

    # Provider SDKs frequently wrap the real transport failure. One level of
    # unwrapping catches those without making every wrapped error retryable.
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        return is_retryable_stream_error(cause)

    return False


def retry_after_seconds(exc: BaseException) -> float | None:
    """The provider's own ``Retry-After``, when it sent one.

    pydantic-ai 2.19+ parses the header onto ``ModelHTTPError``. Honouring it
    matters for 429s: backing off less than the provider asked for just earns
    another 429.
    """
    if not isinstance(exc, ModelHTTPError):
        return None
    value = getattr(exc, "retry_after", None)
    if value is None:
        return None
    try:
        seconds = float(value)
    except TypeError, ValueError:
        return None
    return seconds if seconds >= 0 else None
