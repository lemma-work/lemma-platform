"""A model response that stops making progress has to end, not hang.

The failure these pin is the one dev hit: a provider that keeps sending
*something* — a token a second — while the per-chunk read timeout resets on
every chunk and never fires. Nothing timed out, nothing retried, and the
person's request stayed open for 270s holding the conversation's active run.

Two of these matter more than the rest and are worth naming:

* ``test_the_error_raised_is_one_the_run_already_knows_how_to_retry`` is the
  load-bearing one. The budget is only a fix because the error it raises routes
  into ``drive_with_retry``; raising anything else would turn a slow provider
  into a failed run, which is worse than the hang.
* ``test_a_steady_long_answer_is_not_cut_off`` is the one that keeps this from
  being a regression. A budget that fires on legitimate work would be a far
  more expensive bug than the one being fixed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from app.modules.agent.infrastructure.transport_errors import (
    is_retryable_stream_error,
)
from app.modules.agent.services.model_stream_budget import (
    ModelStreamBudgetTransport,
    budgeted_stream,
)

A_REQUEST = httpx.Request("POST", "https://provider.example/v1/chat/completions")


class _Body(httpx.AsyncByteStream):
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

    with pytest.raises(httpx.ReadTimeout) as raised:
        await _drain(_budgeted(body, first_chunk=0.05, total=None))

    assert "no response body" in str(raised.value)
    assert body.delivered == 0


@pytest.mark.asyncio
async def test_a_trickling_provider_is_given_up_on() -> None:
    """The actual production failure, in miniature.

    Chunks keep arriving, so a per-chunk timeout is reset forever and the
    exchange never ends. Only a total bound sees it — note the first chunk
    arrives promptly here, so the first-chunk bound is deliberately generous
    and takes no part in catching this.
    """
    body = _Body(chunks=1000, gap=0.01)

    with pytest.raises(httpx.ReadTimeout) as raised:
        await _drain(_budgeted(body, first_chunk=5.0, total=0.1))

    assert "stopped making progress" in str(raised.value)
    # It really was making *some* progress, which is the whole difficulty.
    assert body.delivered > 0


@pytest.mark.asyncio
async def test_the_error_raised_is_one_the_run_already_knows_how_to_retry() -> None:
    """The reason this fix is a fix rather than a different failure.

    ``httpx.ReadTimeout`` is an ``httpx.TransportError``, which
    ``is_retryable_stream_error`` already answers True for, so the run re-enters
    the graph from the snapshot taken before the failing request instead of
    dying. Pinned here because it is an invariant across two modules that
    nothing else would catch if either side moved.
    """
    body = _Body(chunks=1000, gap=0.01)

    with pytest.raises(httpx.ReadTimeout) as raised:
        await _drain(_budgeted(body, first_chunk=None, total=0.1))

    assert is_retryable_stream_error(raised.value) is True


@pytest.mark.asyncio
async def test_the_connection_is_not_kept_for_a_provider_we_gave_up_on() -> None:
    """Abandoning the body without closing it would hold a pooled connection.

    That is the state this module exists to end, so a stream left open on the
    way out would defeat the point while still passing every other test here.
    """
    body = _Body(chunks=1000, gap=0.01)

    with pytest.raises(httpx.ReadTimeout):
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
    """End to end through httpx, because that is how it is actually wired.

    Direct tests of the stream prove the bound; this proves it survives being
    installed as a transport, where httpx — not the test — drives the body.
    """
    body = _Body(chunks=1000, gap=0.01)
    transport = ModelStreamBudgetTransport(
        httpx.MockTransport(lambda request: httpx.Response(200, stream=body)),
        first_chunk_seconds=None,
        total_seconds=0.1,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post("https://provider.example/v1/chat/completions")

    assert body.closed is True


@pytest.mark.asyncio
async def test_the_transport_leaves_a_healthy_response_alone() -> None:
    body = _Body(chunks=4, gap=0.0)
    transport = ModelStreamBudgetTransport(
        httpx.MockTransport(lambda request: httpx.Response(200, stream=body)),
        first_chunk_seconds=1.0,
        total_seconds=5.0,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post("https://provider.example/v1/chat/completions")

    assert response.status_code == 200
    assert response.content == b"data: token\n\n" * 4
