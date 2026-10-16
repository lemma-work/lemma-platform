"""Transport-agnostic outbound delivery retry policy for agent surfaces.

Platform services build their own payloads against their own transports (httpx,
slack_sdk, aiohttp), so the shared piece is *retry + classification*, not an
HTTP client. ``with_retry`` runs a send callable, asks a platform-supplied
``classify`` whether a failure is transient, and retries transient failures
with bounded exponential backoff, honouring a server's ``Retry-After``.

What it cannot wait out inline it reports instead: a caller that wraps its
sends in :func:`track_sends` learns that the platform asked for more time than
an inline retry should spend (or that every attempt hit a transient failure),
and how long it asked for. The final answer's sender uses that to hand the
reply to the destination pump's durable retry (``surface_deliveries``) rather
than lose it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

import aiohttp
import httpx

from app.modules.agent_surfaces.domain.delivery_limits import MAX_DELIVERY_ATTEMPTS

T = TypeVar("T")


class DeliveryClassification(Enum):
    """Whether a failed delivery attempt is worth retrying."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


#: Failures that happen before a request reaches the platform: no connection
#: was made, so nothing was sent and a retry cannot post the message twice.
_NEVER_SENT: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    aiohttp.ClientConnectorError,
    aiohttp.ConnectionTimeoutError,
)


def classify_transport_failure(exc: Exception) -> DeliveryClassification:
    """Whether a send that failed in transport may be sent again.

    Only when it provably never left: a read timeout or a dropped connection
    after the request went out is exactly when the platform may already have
    posted it, and a retry is then the duplicate the person sees. Those are
    not retried -- a missing message is reported; a doubled one cannot be
    taken back.
    """
    if isinstance(exc, _NEVER_SENT):
        return DeliveryClassification.TRANSIENT
    return DeliveryClassification.PERMANENT


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = MAX_DELIVERY_ATTEMPTS
    base_delay: float = 0.5
    max_delay: float = 8.0
    #: The longest ``Retry-After`` waited out inside the send. Slack and
    #: Telegram both ask for tens of seconds on a busy chat, and retrying early
    #: inside a rate limit only extends it; past this the wait belongs to a
    #: deferred retry, not to the request holding the send.
    max_retry_after: float = 60.0

    def backoff(self, attempt: int, *, retry_after: float | None = None) -> float:
        """Delay before the ``attempt``-th retry (1-based).

        A positive ``retry_after`` (e.g. Telegram 429 ``parameters.retry_after``)
        wins over exponential backoff and is honoured in full, up to
        ``max_retry_after``; :func:`with_retry` does not wait longer than that.
        """
        if retry_after is not None and retry_after > 0:
            return min(float(retry_after), self.max_retry_after)
        return min(self.base_delay * (2 ** max(attempt - 1, 0)), self.max_delay)


@dataclass
class SendOutcome:
    """What the sends inside one :func:`track_sends` block ran into."""

    #: A transient failure that retrying inline did not get past: attempts ran
    #: out, or the platform asked for longer than ``max_retry_after``.
    gave_up_transient: bool = False
    #: The longest wait the platform asked for, in seconds.
    retry_after: float | None = None
    #: Part of a multi-part message reached the person before a later part
    #: failed. Sending the whole message again would repeat that part.
    partial: bool = False


_outcome: ContextVar[SendOutcome | None] = ContextVar(
    "surface_send_outcome", default=None
)


@contextmanager
def track_sends() -> Iterator[SendOutcome]:
    """Record what the sends inside this block ran into."""
    outcome = SendOutcome()
    token = _outcome.set(outcome)
    try:
        yield outcome
    finally:
        _outcome.reset(token)


def note_partial_delivery() -> None:
    """Say that part of a multi-part message was delivered before a failure."""
    outcome = _outcome.get()
    if outcome is not None:
        outcome.partial = True


def _note_gave_up(retry_after: float | None) -> None:
    outcome = _outcome.get()
    if outcome is None:
        return
    outcome.gave_up_transient = True
    if retry_after is not None and retry_after > (outcome.retry_after or 0.0):
        outcome.retry_after = retry_after


async def with_retry(
    send: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    classify: Callable[[Exception], DeliveryClassification],
    retry_after: Callable[[Exception], float | None] | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``send`` with bounded retry on transient failures.

    ``classify`` decides transient-vs-permanent per exception. Permanent
    failures (and the final attempt) re-raise. A ``Retry-After`` longer than
    the policy waits inline re-raises at once rather than spending an attempt
    early inside the limit; a tracked caller is told how long was asked for.
    ``sleep`` is injectable for tests.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await send()
        except Exception as exc:  # noqa: BLE001 - re-raised after classification
            if classify(exc) is DeliveryClassification.PERMANENT:
                raise
            wait = retry_after(exc) if retry_after is not None else None
            too_long = wait is not None and wait > policy.max_retry_after
            if too_long or attempt >= policy.max_attempts:
                _note_gave_up(wait)
                raise
            delay = policy.backoff(attempt, retry_after=wait)
            if on_retry is not None:
                on_retry(attempt, exc)
            await sleep(delay)
