"""A model response that stops making progress, or runs too long, has to end.

The failure these pin: a provider that keeps sending *something* -- a token at
a time -- while the per-chunk read timeout resets on every chunk and never
fires, so the person's request stays open holding the conversation's run slot.

Three of these matter more than the rest and are worth naming:

* ``test_a_provider_that_never_starts_is_retried_as_a_drop`` -- a stalled first
  chunk is a transient failure, and its error routes into ``drive_with_retry``.
* ``test_a_provider_that_never_sends_headers_is_given_up_on_quickly`` -- the
  wait for headers is inside the first-chunk bound; outside it, only the
  per-chunk read timeout ended the request, minutes later.
* ``test_an_over_long_answer_is_not_mistaken_for_a_dropped_connection`` -- an
  answer past the whole-response budget would take as long again, so it is a
  different error with a different, stricter retry.
* ``test_a_steady_long_answer_is_not_cut_off`` is the one that keeps this from
  being a regression. A budget that fires on legitimate work would be a far
  more expensive bug than the one being fixed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx2
import pytest

from app.modules.agent.infrastructure.transport_errors import (
    ModelResponseBudgetExceeded,
    is_retryable_stream_error,
    response_budget_message,
)
from app.modules.agent.services.model_stream_budget import (
    ModelStreamBudgetTransport,
    budgeted_stream,
)

A_REQUEST = httpx2.Request("POST", "https://provider.example/v1/chat/completions")


class _Body(httpx2.AsyncByteStream):
    """A response body on a script: wait this long, then send this.

    Records whether it was closed, because "the connection is not kept" is half
    of what is being fixed and is otherwise invisible from the outside.
    """

    def __init__(self, *, chunks: int, gap: float, lead_in: float | None = None):
        self.chunks = chunks
        self.gap = gap
        self.lead_in = gap if lead_in is None else lead_in
        self.closed = False
        self.delivered = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index in range(self.chunks):
            await asyncio.sleep(self.lead_in if index == 0 else self.gap)
            self.delivered += 1
            yield b"data: token\n\n"

    async def aclose(self) -> None:
        self.closed = True


def _budgeted(body: _Body, *, first_chunk: float | None, total: float | None):
    return budgeted_stream(
        body,
        request=A_REQUEST,
        first_chunk_seconds=first_chunk,
        total_seconds=total,
    )


async def _drain(stream) -> int:
    received = 0
    async for _ in stream:
        received += 1
    return received


@pytest.mark.asyncio
async def test_a_provider_that_never_starts_is_given_up_on() -> None:
    """Accepted the request, sent headers, then nothing.

    The per-chunk read timeout cannot help here on any sane setting, because
    the setting that would catch it quickly is the one that kills legitimate
    long answers. A first-chunk bound is a different question with a different
    answer.
    """
    body = _Body(chunks=3, gap=0.01, lead_in=5.0)

    with pytest.raises(httpx2.ReadTimeout) as raised:
        await _drain(_budgeted(body, first_chunk=0.05, total=None))

    assert "no response body" in str(raised.value)
    assert body.delivered == 0


@pytest.mark.asyncio
async def test_a_trickling_provider_is_given_up_on() -> None:
    """The failure this module exists for, in miniature.

    Chunks keep arriving, so a per-chunk timeout is reset forever and the
    exchange never ends. Only a total bound sees it -- note the first chunk
    arrives promptly here, so the first-chunk bound is deliberately generous
    and takes no part in catching this.
    """
    body = _Body(chunks=1000, gap=0.01)

    with pytest.raises(ModelResponseBudgetExceeded) as raised:
        await _drain(_budgeted(body, first_chunk=5.0, total=0.1))

    assert "ran past its time budget" in str(raised.value)
    assert raised.value.budget_seconds == 0.1
    # It really was making *some* progress, which is the whole difficulty.
    assert body.delivered > 0


@pytest.mark.asyncio
async def test_a_provider_that_never_starts_is_retried_as_a_drop() -> None:
    """A stalled first chunk is transient: the run re-asks the request."""
    body = _Body(chunks=3, gap=0.01, lead_in=5.0)

    with pytest.raises(httpx2.ReadTimeout) as raised:
        await _drain(_budgeted(body, first_chunk=0.05, total=10.0))

    assert not isinstance(raised.value, ModelResponseBudgetExceeded)
    assert is_retryable_stream_error(raised.value) is True


@pytest.mark.asyncio
async def test_an_over_long_answer_is_not_mistaken_for_a_dropped_connection() -> None:
    """Nothing dropped: the same request sent again takes as long again.

    So it is not a transient transport error, and what the reader is told says
    the model took too long -- never that the connection kept dropping, which
    sends them round the same over-long request.
    """
    body = _Body(chunks=1000, gap=0.01)

    with pytest.raises(ModelResponseBudgetExceeded) as raised:
        await _drain(_budgeted(body, first_chunk=None, total=0.1))

    assert is_retryable_stream_error(raised.value) is False
    message = response_budget_message(
        ModelResponseBudgetExceeded(
            "x", request=A_REQUEST, elapsed_seconds=900.0, budget_seconds=900.0
        )
    )
    assert "took longer than 15 minutes" in message
    assert "dropping" not in message


@pytest.mark.asyncio
async def test_the_whole_response_budget_counts_from_when_the_request_was_sent() -> (
    None
):
    """Time spent before the provider sent headers is inside the budget.

    Counted from the first body chunk, a slow start sat on top of the budget,
    and the real ceiling on one exchange was the budget plus the read timeout.
    """
    body = _Body(chunks=1000, gap=0.01)

    async def slow_headers(request: httpx2.Request) -> httpx2.Response:
        del request
        await asyncio.sleep(0.15)
        return httpx2.Response(200, stream=body)

    transport = ModelStreamBudgetTransport(
        httpx2.MockTransport(slow_headers), first_chunk_seconds=None, total_seconds=0.2
    )

    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(ModelResponseBudgetExceeded) as raised:
            await client.post("https://provider.example/v1/chat/completions")

    assert raised.value.elapsed_seconds >= 0.2
    # Only the time left after the slow start was spent on the body.
    assert body.delivered < 10


def _slow_headers(seconds: float, body: _Body):
    async def respond(request: httpx2.Request) -> httpx2.Response:
        del request
        await asyncio.sleep(seconds)
        return httpx2.Response(200, stream=body)

    return httpx2.MockTransport(respond)


@pytest.mark.asyncio
async def test_a_provider_that_never_sends_headers_is_given_up_on_quickly() -> None:
    """Accepted the request and never answered: not even headers.

    Seen in development on 2026-10-28: a glm-5.3-flash request waited the full
    180s read timeout for headers while the first-chunk bound was 60s, and the
    retry then answered in a second. The bound has to cover the headers too,
    and what it raises has to be the retryable kind.
    """
    body = _Body(chunks=3, gap=0.0)
    transport = ModelStreamBudgetTransport(
        _slow_headers(5.0, body), first_chunk_seconds=0.05, total_seconds=10.0
    )

    async with httpx2.AsyncClient(transport=transport) as client:
        started = asyncio.get_running_loop().time()
        with pytest.raises(httpx2.ReadTimeout) as raised:
            await client.post("https://provider.example/v1/chat/completions")
        waited = asyncio.get_running_loop().time() - started

    assert waited < 1.0
    assert "no response headers" in str(raised.value)
    assert not isinstance(raised.value, ModelResponseBudgetExceeded)
    assert is_retryable_stream_error(raised.value) is True


@pytest.mark.asyncio
async def test_headers_that_take_past_the_whole_budget_are_an_over_long_answer() -> (
    None
):
    """With only the total bound set, a header wait past it is that bound firing."""
    body = _Body(chunks=3, gap=0.0)
    transport = ModelStreamBudgetTransport(
        _slow_headers(5.0, body), first_chunk_seconds=None, total_seconds=0.05
    )

    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(ModelResponseBudgetExceeded):
            await client.post("https://provider.example/v1/chat/completions")


@pytest.mark.asyncio
async def test_the_first_chunk_bound_counts_from_when_the_request_was_sent() -> None:
    """Slow headers then a slow first chunk: together they are one wait.

    Counted from the headers, each half could take the whole bound and the
    request still stalled for twice as long as the bound says.
    """
    body = _Body(chunks=3, gap=0.0, lead_in=0.15)
    transport = ModelStreamBudgetTransport(
        _slow_headers(0.15, body), first_chunk_seconds=0.2, total_seconds=10.0
    )

    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx2.ReadTimeout) as raised:
            await client.post("https://provider.example/v1/chat/completions")

    assert "no response body" in str(raised.value)
    assert body.delivered == 0


@pytest.mark.asyncio
async def test_slow_headers_inside_the_bound_are_untouched() -> None:
    body = _Body(chunks=3, gap=0.0)
    transport = ModelStreamBudgetTransport(
        _slow_headers(0.05, body), first_chunk_seconds=1.0, total_seconds=5.0
    )

    async with httpx2.AsyncClient(transport=transport) as client:
        response = await client.post("https://provider.example/v1/chat/completions")

    assert response.content == b"data: token\n\n" * 3


@pytest.mark.asyncio
async def test_the_connection_is_not_kept_for_a_provider_we_gave_up_on() -> None:
    """Abandoning the body without closing it would hold a pooled connection.

    That is the state this module exists to end, so a stream left open on the
    way out would defeat the point while still passing every other test here.
    """
    body = _Body(chunks=1000, gap=0.01)

    with pytest.raises(httpx2.ReadTimeout):
        await _drain(_budgeted(body, first_chunk=None, total=0.1))

    assert body.closed is True


@pytest.mark.asyncio
async def test_a_steady_long_answer_is_not_cut_off() -> None:
    """The regression this must not introduce.

    An answer that streams for a long time while genuinely producing tokens is
    ordinary agent work. A bound that fires on it would break real runs to fix
    a rare one, so the budget is sized well above the work, not near it.
    """
    body = _Body(chunks=40, gap=0.005)

    received = await _drain(_budgeted(body, first_chunk=1.0, total=5.0))

    assert received == 40
    assert body.closed is False


@pytest.mark.asyncio
async def test_a_prompt_response_is_untouched() -> None:
    """The overwhelming majority of requests, which must not change at all."""
    body = _Body(chunks=3, gap=0.0)

    assert await _drain(_budgeted(body, first_chunk=1.0, total=5.0)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [0.0, None])
async def test_a_budget_can_be_switched_off(setting) -> None:
    """Zero means "do not bound this", so an operator can rule this out.

    Worth a test because the check is `<= 0` rather than falsy: a budget is a
    float from settings, and the day somebody sets it to something odd, the
    behaviour should be the documented one.
    """
    body = _Body(chunks=3, gap=0.01)

    assert await _drain(_budgeted(body, first_chunk=setting, total=setting)) == 3


@pytest.mark.asyncio
async def test_the_transport_bounds_a_real_client_request() -> None:
    """End to end through httpx2, because that is how it is actually wired.

    Direct tests of the stream prove the bound; this proves it survives being
    installed as a transport, where httpx2 — not the test — drives the body.
    """
    body = _Body(chunks=1000, gap=0.01)
    transport = ModelStreamBudgetTransport(
        httpx2.MockTransport(lambda request: httpx2.Response(200, stream=body)),
        first_chunk_seconds=None,
        total_seconds=0.1,
    )

    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx2.ReadTimeout):
            await client.post("https://provider.example/v1/chat/completions")

    assert body.closed is True


@pytest.mark.asyncio
async def test_the_transport_leaves_a_healthy_response_alone() -> None:
    body = _Body(chunks=4, gap=0.0)
    transport = ModelStreamBudgetTransport(
        httpx2.MockTransport(lambda request: httpx2.Response(200, stream=body)),
        first_chunk_seconds=1.0,
        total_seconds=5.0,
    )

    async with httpx2.AsyncClient(transport=transport) as client:
        response = await client.post("https://provider.example/v1/chat/completions")

    assert response.status_code == 200
    assert response.content == b"data: token\n\n" * 4
