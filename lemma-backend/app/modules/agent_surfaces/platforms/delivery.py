"""Transport-agnostic outbound delivery retry policy for agent surfaces.

Platform services build their own payloads against their own transports (httpx,
slack_sdk, aiohttp), so the shared piece is *retry + classification*, not an
HTTP client. ``with_retry`` runs a send callable, asks a platform-supplied
``classify`` whether a failure is transient, and retries transient failures
with bounded exponential backoff, honouring a server's ``Retry-After``.

What it cannot wait out inline it reports instead: a caller that wraps its
sends in :func:`track_sends` learns that the platform asked for more time than
an inline retry should spend (or that every attempt hit a transient failure),
and how long it asked for. A sender with a durable retry -- a channel
delivery, the destination pump's ``surface_deliveries`` -- uses that to try
again later rather than lose the message, and never repeats one that partly
arrived.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
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
    #: A platform was called at all (``note_tried``). A send that ended with
    #: nothing tried found nowhere to send, or nothing left to send.
    tried: bool = False
    #: The platform last called, as ``note_tried`` named it.
    platform: str | None = None
    #: A call ended in a way that does not say whether the platform took it
    #: (a read timeout, a dropped connection after the request went out).
    ambiguous: bool = False
    #: The HTTP status of the last failure the platform answered with, as a
    #: delivery's try records it (``channel_delivery_attempts``).
    provider_status: str | None = None
    #: The block this one is inside: what is noted here is noted there too.
    parent: SendOutcome | None = field(default=None, repr=False, compare=False)

    def _chain(self) -> Iterator[SendOutcome]:
        outcome: SendOutcome | None = self
        while outcome is not None:
            yield outcome
            outcome = outcome.parent


_outcome: ContextVar[SendOutcome | None] = ContextVar(
    "surface_send_outcome", default=None
)


@contextmanager
def track_sends() -> Iterator[SendOutcome]:
    """Record what the sends inside this block ran into. Nested blocks each
    see their own sends, and an outer block sees every send inside it."""
    outcome = SendOutcome(parent=_outcome.get())
    token = _outcome.set(outcome)
    try:
        yield outcome
    finally:
        _outcome.reset(token)


def _tracked() -> Iterator[SendOutcome]:
    outcome = _outcome.get()
    return outcome._chain() if outcome is not None else iter(())


def note_partial_delivery() -> None:
    """Say that part of a multi-part message was delivered before a failure."""
    for outcome in _tracked():
        outcome.partial = True


def note_tried(platform: str | None = None) -> None:
    """Say that a platform is about to be called with the message."""
    for outcome in _tracked():
        outcome.tried = True
        if platform is not None:
            outcome.platform = platform


def may_have_arrived(exc: BaseException) -> bool:
    """Whether a send that failed with ``exc`` may still have reached the
    platform: it failed in transport after the request left, rather than
    before (``_NEVER_SENT``) or with the platform's own answer."""
    if isinstance(exc, _NEVER_SENT):
        return False
    if isinstance(exc, httpx.HTTPStatusError | aiohttp.ClientResponseError):
        return False
    return isinstance(
        exc, httpx.TransportError | aiohttp.ClientError | TimeoutError | OSError
    )


def _note_gave_up(retry_after: float | None) -> None:
    for outcome in _tracked():
        outcome.gave_up_transient = True
        if retry_after is not None and retry_after > (outcome.retry_after or 0.0):
            outcome.retry_after = retry_after


def _note_status(exc: BaseException) -> None:
    status: int | None = None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    elif isinstance(exc, aiohttp.ClientResponseError):
        status = exc.status
    elif isinstance(code := getattr(exc, "status_code", None), int):
        # A platform client's own error (``WhatsAppApiError``,
        # ``TelegramApiError``), carrying the HTTP status it was answered with.
        status = code
    if status is None:
        return
    for outcome in _tracked():
        outcome.provider_status = str(status)


def _note_ended(exc: BaseException) -> None:
    if not may_have_arrived(exc):
        return
    for outcome in _tracked():
        outcome.ambiguous = True


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
            _note_status(exc)
            if classify(exc) is DeliveryClassification.PERMANENT:
                _note_ended(exc)
                raise
            wait = retry_after(exc) if retry_after is not None else None
            too_long = wait is not None and wait > policy.max_retry_after
            if too_long or attempt >= policy.max_attempts:
                _note_gave_up(wait)
                _note_ended(exc)
                raise
            delay = policy.backoff(attempt, retry_after=wait)
            if on_retry is not None:
                on_retry(attempt, exc)
            await sleep(delay)
