"""Ending a model response that has stopped making progress.

The provider client's read timeout is *per chunk*: httpx resets it every time
bytes arrive, which is the right shape for "the provider has gone away" and the
reason a legitimately long answer can stream for minutes without tripping it.

It cannot see the other failure, and that is the one production hit. A provider
that keeps sending *something* — a token every half-second, a keep-alive frame —
never trips a per-chunk timeout however long it goes on. Measured on dev: two
consecutive requests took 85s and 185s to deliver 71 and 325 tokens, roughly one
token per second, while fifteen other requests on the same client finished in
0.6-9.0s at forty to ninety tokens per second. Nothing timed out, nothing
retried, and the person's HTTP request stayed open the whole time holding the
conversation's one active run slot.

So this bounds the two things a per-chunk timeout structurally cannot:

* how long the provider may take to send the *first* body chunk, which is where
  a request that was accepted but never really started shows itself, and
* how long one exchange may run in total, which is the only thing that catches
  a trickle.

Both raise :class:`httpx.ReadTimeout`. That is not decoration: it is an
``httpx.TransportError``, so ``is_retryable_stream_error`` already returns True
for it and ``drive_with_retry`` re-enters the run from the snapshot taken before
the failing request — re-asking only that request, replaying completed tool
results rather than re-running them. Raising the transport's own error is what
lets an existing, tested recovery path handle a new failure with no changes to
it.

The stream is closed on the way out rather than abandoned, so a provider that
has stopped making progress does not keep a pooled connection with it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import httpx

from app.core.log.log import get_logger

logger = get_logger(__name__)

__all__ = ["ModelStreamBudgetTransport", "budgeted_stream"]


def _disabled(seconds: float | None) -> bool:
    """A budget of zero (or less, or nothing) means "do not bound this"."""
    return seconds is None or seconds <= 0


class _BudgetedByteStream(httpx.AsyncByteStream):
    """Wraps a response body, failing it when progress stops.

    Each chunk is awaited under its own deadline rather than the whole
    iteration under one. Spanning a ``yield`` with a timeout would put the
    cancellation in whichever task happened to be consuming the generator
    instead of this one — the same task-bound cancel-scope hazard the harness
    documents at length — and a timer per chunk is the cost of not having it.
    """

    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        *,
        request: httpx.Request,
        first_chunk_seconds: float | None,
        total_seconds: float | None,
    ) -> None:
        self._stream = stream
        self._request = request
        self._first_chunk_seconds = first_chunk_seconds
        self._total_seconds = total_seconds

    def _budget(self, *, started: float, first_chunk: bool) -> float | None:
        """How long the next chunk may take. ``None`` means "unbounded"."""
        budgets: list[float] = []
        if first_chunk and not _disabled(self._first_chunk_seconds):
            budgets.append(float(self._first_chunk_seconds))  # type: ignore[arg-type]
        if not _disabled(self._total_seconds):
            elapsed = time.monotonic() - started
            budgets.append(float(self._total_seconds) - elapsed)  # type: ignore[arg-type]
        return min(budgets) if budgets else None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        started = time.monotonic()
        iterator = self._stream.__aiter__()
        first_chunk = True
        while True:
            budget = self._budget(started=started, first_chunk=first_chunk)
            try:
                if budget is None:
                    chunk = await iterator.__anext__()
                else:
                    # Negative budgets are possible when the total elapsed while
                    # the consumer held the last chunk; asyncio.timeout fires
                    # immediately on those, which is the intended answer.
                    async with asyncio.timeout(budget):
                        chunk = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise await self._expired(
                    started=started, first_chunk=first_chunk
                ) from exc
            first_chunk = False
            yield chunk

    async def _expired(self, *, started: float, first_chunk: bool) -> httpx.ReadTimeout:
        """Close the connection, say why, and build the error to raise."""
        elapsed = time.monotonic() - started
        reason = (
            "the provider sent no response body"
            if first_chunk
            else "the provider stopped making progress"
        )
        # Closed rather than left to the pool: a stream abandoned mid-body would
        # otherwise hold a connection open for a provider we have just given up
        # on, which is the state this whole module exists to end.
        await self.aclose()
        logger.warning(
            "agent.model_stream_budget.stream_abandoned.degraded",
            reason=reason,
            elapsed_seconds=round(elapsed, 2),
            first_chunk=first_chunk,
            url=str(self._request.url),
        )
        return httpx.ReadTimeout(
            f"Model stream abandoned after {elapsed:.1f}s: {reason}.",
            request=self._request,
        )

    async def aclose(self) -> None:
        await self._stream.aclose()


class ModelStreamBudgetTransport(httpx.AsyncBaseTransport):
    """Applies :class:`_BudgetedByteStream` to every response it passes on.

    A transport rather than something further up because this has to sit under
    every provider SDK we use: the budget then holds for anything reached over
    the shared client, and the error it raises is the one the SDKs and the retry
    layer already understand.
    """

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        *,
        first_chunk_seconds: float | None,
        total_seconds: float | None,
    ) -> None:
        self._wrapped = wrapped
        self._first_chunk_seconds = first_chunk_seconds
        self._total_seconds = total_seconds

    @property
    def wrapped(self) -> httpx.AsyncBaseTransport:
        return self._wrapped

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._wrapped.handle_async_request(request)
        if _disabled(self._first_chunk_seconds) and _disabled(self._total_seconds):
            return response
        response.stream = budgeted_stream(
            response.stream,  # type: ignore[arg-type]
            request=request,
            first_chunk_seconds=self._first_chunk_seconds,
            total_seconds=self._total_seconds,
        )
        return response

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def budgeted_stream(
    stream: httpx.AsyncByteStream,
    *,
    request: httpx.Request,
    first_chunk_seconds: float | None,
    total_seconds: float | None,
) -> httpx.AsyncByteStream:
    """A response body that fails rather than trickling forever."""
    return _BudgetedByteStream(
        stream,
        request=request,
        first_chunk_seconds=first_chunk_seconds,
        total_seconds=total_seconds,
    )
