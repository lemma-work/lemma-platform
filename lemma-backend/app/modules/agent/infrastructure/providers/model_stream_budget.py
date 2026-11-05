"""Ending a model response that has stopped making progress, or run too long.

The provider client's read timeout is *per chunk*: httpx2 resets it every time
bytes arrive, which is the right shape for "the provider has gone away" and the
reason a legitimately long answer can stream for minutes without tripping it.

It cannot see the other failure. A provider that keeps sending *something* -- a
token every few seconds, a keep-alive frame, a long stream of reasoning -- never
trips a per-chunk timeout however long it goes on, and meanwhile the person's
conversation holds its one active run slot.

So this bounds the two things a per-chunk timeout structurally cannot:

* how long the provider may take to send the *first* body chunk, counted from
  the moment the request is sent, so a provider that never sends its response
  headers is caught by it too. That is where a request that was accepted but
  never really started shows itself. It raises :class:`httpx2.ReadTimeout`, a
  transport error the harness already retries from the snapshot taken before
  the failing request.
* how long one exchange may run in total, counted from the moment the request
  is sent. That raises :class:`ModelResponseBudgetExceeded`, which is *not* a
  dropped connection: the same request sent again takes the same time, so the
  harness retries it at most once and with a smaller answer budget, and tells
  the reader the model took too long rather than that the connection dropped.

The stream is closed on the way out rather than abandoned, so a provider that
has stopped making progress does not keep a pooled connection with it.

Everything here is ``httpx2``, the HTTP stack the provider SDKs are built on,
because this transport sits inside their client.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import httpx2

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.providers.transport_errors import (
    ModelResponseBudgetExceeded,
)

logger = get_logger(__name__)

__all__ = ["ModelStreamBudgetTransport", "budgeted_stream"]


def _disabled(seconds: float | None) -> bool:
    """A budget of zero (or less, or nothing) means "do not bound this"."""
    return seconds is None or seconds <= 0


class _BudgetedByteStream(httpx2.AsyncByteStream):
    """Wraps a response body, failing it when progress stops.

    Each chunk is awaited under its own deadline rather than the whole
    iteration under one. Spanning a ``yield`` with a timeout would put the
    cancellation in whichever task happened to be consuming the generator
    instead of this one — the same task-bound cancel-scope hazard the harness
    documents at length — and a timer per chunk is the cost of not having it.
    """

    def __init__(
        self,
        stream: httpx2.AsyncByteStream,
        *,
        request: httpx2.Request,
        first_chunk_seconds: float | None,
        total_seconds: float | None,
        sent_at: float | None = None,
    ) -> None:
        self._stream = stream
        self._request = request
        self._first_chunk_seconds = (
            None if _disabled(first_chunk_seconds) else first_chunk_seconds
        )
        self._total_seconds = None if _disabled(total_seconds) else total_seconds
        # When the request went out. The whole-exchange budget counts from here,
        # so the time a provider spends before sending headers is inside the
        # budget rather than on top of it.
        self._sent_at = sent_at

    async def __aiter__(self) -> AsyncIterator[bytes]:
        sent_at = self._sent_at if self._sent_at is not None else time.monotonic()
        iterator = self._stream.__aiter__()
        first_chunk = True
        while True:
            total_left = (
                None
                if self._total_seconds is None
                else self._total_seconds - (time.monotonic() - sent_at)
            )
            # Counted from when the request went out, like the total: the
            # time the provider took to send headers is already spent.
            first_chunk_left = (
                self._first_chunk_seconds - (time.monotonic() - sent_at)
                if first_chunk and self._first_chunk_seconds is not None
                else None
            )
            limits = [
                left for left in (total_left, first_chunk_left) if left is not None
            ]
            try:
                if not limits:
                    chunk = await iterator.__anext__()
                else:
                    # Negative budgets are possible when the total elapsed while
                    # the consumer held the last chunk; asyncio.timeout fires
                    # immediately on those, which is the intended answer.
                    async with asyncio.timeout(min(limits)):
                        chunk = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                over_total = total_left is not None and (
                    first_chunk_left is None or total_left <= first_chunk_left
                )
                raise await self._expired(
                    sent_at=sent_at, first_chunk=first_chunk, over_total=over_total
                ) from exc
            first_chunk = False
            yield chunk

    async def _expired(
        self, *, sent_at: float, first_chunk: bool, over_total: bool
    ) -> httpx2.ReadTimeout:
        """Close the connection, say why, and build the error to raise."""
        # Closed rather than left to the pool: a stream abandoned mid-body would
        # otherwise hold a connection open for a provider we have just given up
        # on, which is the state this whole module exists to end.
        await self.aclose()
        return _abandoned(
            self._request,
            elapsed=time.monotonic() - sent_at,
            reason=(
                "the response ran past its time budget"
                if over_total
                else "the provider sent no response body"
                if first_chunk
                else "the provider stopped making progress"
            ),
            first_chunk=first_chunk,
            budget_seconds=self._total_seconds if over_total else None,
        )

    async def aclose(self) -> None:
        await self._stream.aclose()


def _abandoned(
    request: httpx2.Request,
    *,
    elapsed: float,
    reason: str,
    first_chunk: bool,
    budget_seconds: float | None,
) -> httpx2.ReadTimeout:
    """Log a response given up on, and the error the retry layer understands."""
    logger.warning(
        "agent.model_stream_budget.stream_abandoned.degraded",
        reason=reason,
        elapsed_seconds=round(elapsed, 2),
        first_chunk=first_chunk,
        url=str(request.url),
    )
    message = f"Model stream abandoned after {elapsed:.1f}s: {reason}."
    if budget_seconds is not None:
        return ModelResponseBudgetExceeded(
            message,
            request=request,
            elapsed_seconds=elapsed,
            budget_seconds=budget_seconds,
        )
    return httpx2.ReadTimeout(message, request=request)


class ModelStreamBudgetTransport(httpx2.AsyncBaseTransport):
    """Applies :class:`_BudgetedByteStream` to every response it passes on.

    A transport rather than something further up because this has to sit under
    every provider SDK we use: the budget then holds for anything reached over
    the shared client, and the error it raises is the one the SDKs and the retry
    layer already understand.
    """

    def __init__(
        self,
        wrapped: httpx2.AsyncBaseTransport,
        *,
        first_chunk_seconds: float | None,
        total_seconds: float | None,
    ) -> None:
        self._wrapped = wrapped
        self._first_chunk_seconds = first_chunk_seconds
        self._total_seconds = total_seconds

    @property
    def wrapped(self) -> httpx2.AsyncBaseTransport:
        return self._wrapped

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        sent_at = time.monotonic()
        first_chunk = (
            None if _disabled(self._first_chunk_seconds) else self._first_chunk_seconds
        )
        total = None if _disabled(self._total_seconds) else self._total_seconds
        if first_chunk is None and total is None:
            return await self._wrapped.handle_async_request(request)
        # The wait for headers is inside both budgets. Left outside them, a
        # provider that accepted the request and never answered was cut off
        # only by the per-chunk read timeout -- minutes, not the first-chunk
        # bound -- and the retry that then answered in a second came late.
        # Awaited in this task, not across a yield, so the timeout is safe.
        limits = [left for left in (first_chunk, total) if left is not None]
        deadline = asyncio.timeout(min(limits))
        try:
            async with deadline:
                response = await self._wrapped.handle_async_request(request)
        except TimeoutError as exc:
            if not deadline.expired():
                raise
            over_total = total is not None and (
                first_chunk is None or total <= first_chunk
            )
            raise _abandoned(
                request,
                elapsed=time.monotonic() - sent_at,
                reason=(
                    "the response ran past its time budget"
                    if over_total
                    else "the provider sent no response headers"
                ),
                first_chunk=True,
                budget_seconds=total if over_total else None,
            ) from exc
        response.stream = budgeted_stream(
            response.stream,  # type: ignore[arg-type]
            request=request,
            first_chunk_seconds=self._first_chunk_seconds,
            total_seconds=self._total_seconds,
            sent_at=sent_at,
        )
        return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def budgeted_stream(
    stream: httpx2.AsyncByteStream,
    *,
    request: httpx2.Request,
    first_chunk_seconds: float | None,
    total_seconds: float | None,
    sent_at: float | None = None,
) -> httpx2.AsyncByteStream:
    """A response body that fails rather than trickling forever."""
    return _BudgetedByteStream(
        stream,
        request=request,
        first_chunk_seconds=first_chunk_seconds,
        total_seconds=total_seconds,
        sent_at=sent_at,
    )
