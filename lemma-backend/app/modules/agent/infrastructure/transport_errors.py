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
import httpx2
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.modules.agent.infrastructure.tools.tool_errors import AgentInputRequired
from app.core.domain.errors import DomainError

__all__ = [
    "RETRYABLE_STATUS_CODES",
    "ModelResponseBudgetExceeded",
    "is_retryable_stream_error",
    "connection_failure_message",
    "local_model_server_down_message",
    "local_model_server_name",
    "response_budget_expiry",
    "response_budget_message",
    "retry_after_seconds",
]

# 408 request timeout, 409 conflict (some gateways use it for "retry"),
# 429 rate limited. Everything >=500 is treated as retryable separately.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})

# Never retried, whatever else they look like: these carry run control flow or a
# deliberate stop, and re-entering the graph would either duplicate a pause or
# fight the thing that asked us to stop.
_NEVER_RETRY: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    DomainError,
    KeyboardInterrupt,
    SystemExit,
    AgentInputRequired,
    UsageLimitExceeded,
    UnexpectedModelBehavior,
)

# Both HTTP stacks, because the provider SDKs and everything else disagree.
# The model clients are `httpx2` (what the anthropic and openai SDKs require),
# while tools, MCP servers and other callers still raise `httpx`. The two
# hierarchies share no base class -- an `httpx2.ReadTimeout` is not an
# `httpx.ReadTimeout` -- so a check naming only one silently stops matching
# the other's failures.
_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    httpx.TransportError,
    httpx2.TransportError,
)


class ModelResponseBudgetExceeded(httpx2.ReadTimeout):
    """One model exchange ran past its whole-response time budget.

    An `httpx2.ReadTimeout`, because it is raised inside the provider client's
    transport and the SDKs recognise only their own stack's timeouts. So every
    layer that already unwinds a stream on one keeps doing so. Told apart from a dropped connection because it is not one:
    nothing dropped, the answer was simply longer than the budget allows, and
    re-sending the identical request takes the same time and expires again --
    paid for in full each time and thrown away. So it is never retried as a
    drop, and the reader is told what actually happened.
    """

    def __init__(
        self,
        message: str,
        *,
        request: httpx2.Request,
        elapsed_seconds: float,
        budget_seconds: float,
    ) -> None:
        super().__init__(message, request=request)
        self.elapsed_seconds = elapsed_seconds
        self.budget_seconds = budget_seconds


def response_budget_expiry(exc: BaseException) -> ModelResponseBudgetExceeded | None:
    """The budget expiry inside whatever a provider SDK wrapped it in."""
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending and len(seen) < 8:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ModelResponseBudgetExceeded):
            return current
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if linked is not None
        )
    return None


def response_budget_message(exc: ModelResponseBudgetExceeded) -> str:
    """What to say about an answer that took longer than the budget allows.

    Never "the connection kept dropping": the connection was fine, and sending
    again on that advice repeats the same over-long request.
    """
    minutes = max(1, round(exc.budget_seconds / 60))
    return (
        f"The model took longer than {minutes} minutes to answer, so the "
        "request was stopped. Everything finished before it is kept \u2014 ask "
        "for a smaller step, or pick a faster model for this teammate "
        "(Settings \u2192 Models)."
    )


def _provider_connection_error(exc: BaseException) -> bool:
    """True for the provider SDKs' own "couldn't reach the API" wrappers.

    Imported lazily and matched by name so this module doesn't hard-depend on
    both SDKs being installed, and so a provider we haven't special-cased still
    benefits when it wraps a transport error (the ``__cause__`` walk below).
    """
    return type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}


def is_retryable_stream_error(exc: BaseException) -> bool:
    """Whether re-entering the run after ``exc`` is worth trying."""
    if isinstance(exc, _NEVER_RETRY):
        return False
    # Has its own, stricter policy in `drive_with_retry`: an over-long answer
    # is not a transient failure, and re-sending it unchanged repeats it.
    if response_budget_expiry(exc) is not None:
        return False

    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        return status in RETRYABLE_STATUS_CODES or status >= 500

    # TransportError covers ReadError/WriteError/ConnectError/ReadTimeout/
    # PoolTimeout/RemoteProtocolError — the whole mid-stream drop family, which
    # is what the production ReadErrors were — in either HTTP stack.
    if isinstance(exc, _TRANSPORT_ERRORS):
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


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
# The two model servers the Models page offers by name, at their default
# ports. Anything else on this computer is "the model server": the port is
# all there is to go on, and guessing a product from it would be wrong more
# often than it helps.
_LOCAL_SERVER_NAMES = {11434: "Ollama", 1234: "LM Studio"}


def local_model_server_down_message(exc: BaseException) -> str | None:
    """The sentence for "the model server on this computer is not running".

    A refused connection to a loopback address is not a flaky network: nothing
    is listening, and "the connection kept dropping, send another message"
    sends the person round the same failure again. Only the address the
    request went to is read -- never the provider's text.
    """
    name = local_model_server_name(exc)
    if name is None:
        return None
    return f"{name} isn't running on this computer. Start it, then send again."


def local_model_server_name(exc: BaseException) -> str | None:
    """What to call the model server a refused loopback connection was for.

    ``None`` when ``exc`` is not a refused connection to this computer. Split
    out so a caller with its own sentence -- the Models page's connection
    test, which has nothing to "send again" -- names the server the same way.
    """
    connect = _find_connect_failure(exc, depth=0)
    if connect is None:
        return None
    try:
        url = connect.request.url
    except RuntimeError:
        # Both stacks raise when an error was built without its request.
        return None
    if url.host not in _LOOPBACK_HOSTS:
        return None
    return _LOCAL_SERVER_NAMES.get(url.port or 0, "The model server")


def _find_connect_failure(
    exc: BaseException, *, depth: int
) -> httpx.ConnectError | httpx2.ConnectError | None:
    """The `ConnectError` inside whatever the provider SDK wrapped it in."""
    if depth > 6:
        return None
    if isinstance(exc, (httpx.ConnectError, httpx2.ConnectError)):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            found = _find_connect_failure(child, depth=depth + 1)
            if found is not None:
                return found
        return None
    for linked in (exc.__cause__, exc.__context__):
        if linked is not None and linked is not exc:
            found = _find_connect_failure(linked, depth=depth + 1)
            if found is not None:
                return found
    return None


def connection_failure_message(exc: BaseException) -> str:
    """What to say about a connection that failed after every retry.

    Nothing was lost -- each completed message was persisted -- so the usual
    answer is "send another message". Unless nothing was listening at all, on
    this computer: then the thing to do is start the model server, and sending
    again is the same refusal.
    """
    return local_model_server_down_message(exc) or (
        "The connection to the model provider kept dropping. Nothing you "
        "sent was lost \u2014 send another message to pick up where it stopped."
    )
