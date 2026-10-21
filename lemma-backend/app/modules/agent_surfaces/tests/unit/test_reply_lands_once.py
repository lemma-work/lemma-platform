"""A reply reaches the person once: not lost to a rate limit, not sent twice.

Each case is a way an answer used to go missing or arrive doubled: a Retry-After
longer than the retry budget, a read timeout retried as if nothing had posted,
emoji counted as one character where Telegram counts two, a Slack overflow part
whose failure deleted and resent the whole answer, and held files released with
a reply that did not carry them. (A rate limit the inline retry cannot wait
out is now the destination pump's: ``test_surface_deliveries_e2e``.)
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import fakeredis
import httpx
import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.caches import (
    PENDING_DISPLAY_PATHS,
    surface_caches,
)
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.envelope import (
    DeliveryReceipt,
    PartDelivery,
    SurfaceEnvelope,
)
from app.modules.agent_surfaces.platforms.delivery import (
    DeliveryClassification,
    RetryPolicy,
    track_sends,
    with_retry,
)
from app.modules.agent_surfaces.platforms.rendering import chunk_text, utf16_length
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.slack.streaming import SlackStreamSurface
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    classify_telegram_send_error,
)
from app.modules.agent_surfaces.platforms.telegram.message_experience import (
    send_chunk,
)
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    classify_whatsapp_error,
)
from app.modules.agent_surfaces.services import (
    egress_service,
    pending_envelope,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    conversation_operations,  # noqa: F401  (autouse fixture)
)
from app.modules.agent_surfaces.services.pending_envelope import RunFiles


class _RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("429")
        self.retry_after = retry_after


def _transient(_: Exception) -> DeliveryClassification:
    return DeliveryClassification.TRANSIENT


def _retry_after(exc: Exception) -> float | None:
    return exc.retry_after if isinstance(exc, _RateLimited) else None


# ── Retry-After: waited out up to a minute, deferred past it ────────────────


async def test_a_retry_after_within_a_minute_is_waited_out_in_full():
    slept: list[float] = []
    attempts = 0

    async def send() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _RateLimited(30)
        return "sent"

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    result = await with_retry(
        send,
        policy=RetryPolicy(),
        classify=_transient,
        retry_after=_retry_after,
        sleep=sleep,
    )

    # Not cut to the eight-second backoff cap, which retried inside the limit.
    assert (result, slept) == ("sent", [30.0])


async def test_a_retry_after_past_a_minute_is_deferred_not_spent():
    attempts = 0

    async def send() -> None:
        nonlocal attempts
        attempts += 1
        raise _RateLimited(120)

    with track_sends() as outcome, pytest.raises(_RateLimited):
        await with_retry(
            send, policy=RetryPolicy(), classify=_transient, retry_after=_retry_after
        )

    assert attempts == 1
    assert outcome.gave_up_transient is True
    assert outcome.retry_after == 120


class _Redis(fakeredis.FakeAsyncRedis):
    """fakeredis without Lua: the one script the claims run, compare-and-delete."""

    async def eval(self, _script: str, _numkeys: int, key: str, expected: str) -> int:  # type: ignore[override]
        if await self.get(key) != expected:
            return 0
        return await self.delete(key)


def _fake_store(prefix: str) -> RedisJsonCache:
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix=prefix, ttl_seconds=60
    )
    cache._redis = _Redis(decode_responses=True)
    return cache


@pytest.fixture
def claims() -> Iterator[RedisJsonCache]:
    """The Redis the held files live in, in memory."""
    store = _fake_store("test:held-files")
    with surface_caches.substituted(PENDING_DISPLAY_PATHS, store):
        yield store


# ── a send that may have landed is not sent again ───────────────────────────


@pytest.mark.parametrize(
    "classify", [classify_telegram_send_error, classify_whatsapp_error]
)
async def test_a_read_timeout_on_a_send_is_not_retried(classify):
    attempts = 0

    async def send() -> None:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("no answer")

    with pytest.raises(httpx.ReadTimeout):
        await with_retry(send, policy=RetryPolicy(base_delay=0.0), classify=classify)

    assert attempts == 1


@pytest.mark.parametrize(
    "classify", [classify_telegram_send_error, classify_whatsapp_error]
)
async def test_a_connection_never_made_is_retried(classify):
    attempts = 0

    async def send() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("no route")
        return "sent"

    assert (
        await with_retry(send, policy=RetryPolicy(base_delay=0.0), classify=classify)
        == "sent"
    )


async def test_slack_names_each_message_once_across_its_retries(monkeypatch):
    posts: list[dict[str, Any]] = []

    async def post(self, **kwargs: Any) -> dict[str, bool]:
        posts.append(kwargs)
        if len(posts) == 1:
            raise SlackApiError("boom", SimpleNamespace(status_code=503, headers={}))
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", post)
    service = SlackPlatformService(credentials={"access_token": "xoxb-test"})
    service._retry_policy = RetryPolicy(base_delay=0.0, max_delay=0.0)

    await service._post_message(AsyncWebClient(token="x"), {"channel": "C1"})

    assert len(posts) == 2
    assert posts[0]["client_msg_id"] == posts[1]["client_msg_id"]


async def test_a_slack_post_reports_the_ts_it_can_be_edited_by(monkeypatch):
    async def post(self, **kwargs: Any) -> dict[str, object]:
        return {"ok": True, "ts": "1700000000.0001"}

    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", post)
    service = SlackPlatformService(credentials={"access_token": "xoxb-test"})

    ts = await service._post_message(AsyncWebClient(token="x"), {"channel": "C1"})

    assert ts == "1700000000.0001"


# ── Telegram counts UTF-16, and "too long" is split, not lost ───────────────


def test_an_emoji_heavy_answer_is_chunked_under_telegrams_real_limit():
    chunks = chunk_text("😀" * 3000, limit=4096, length=utf16_length)

    assert len(chunks) > 1
    assert all(utf16_length(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks) == "😀" * 3000


async def test_a_chunk_telegram_calls_too_long_goes_out_in_parts():
    sent: list[dict[str, Any]] = []

    async def call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = payload.get("rich_message", {}).get("markdown") or payload.get("text")
        if len(text) > 50:
            raise TelegramApiError(
                method=method,
                status_code=400,
                description="Bad Request: message is too long",
            )
        sent.append(payload)
        return {"ok": True}

    words = " ".join(f"word{index}" for index in range(15))
    await send_chunk(
        call, {"chat_id": 1, "reply_markup": {"inline_keyboard": []}}, words
    )

    texts = [payload["rich_message"]["markdown"] for payload in sent]
    assert " ".join(texts) == words
    # The buttons belong to the message once, not to every part.
    assert sum("reply_markup" in payload for payload in sent) == 1


# ── Slack: once the stream carries the answer, an overflow failure is final ──


def _slack_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="C1",
        external_thread_id="100.0",
        external_message_id="100.0",
        message_text="hi",
        reply_target={"channel": "C1", "thread_ts": "100.0"},
    )


async def test_a_failed_overflow_part_does_not_resend_the_whole_answer(monkeypatch):
    posts: list[dict[str, Any]] = []

    async def ok(self, **_: Any) -> dict[str, Any]:
        return {"ok": True, "ts": "200.5", "channel": "C1"}

    async def post(self, **kwargs: Any) -> dict[str, Any]:
        posts.append(kwargs)
        if len(posts) == 2:
            raise SlackApiError("internal", SimpleNamespace(status_code=400))
        return {"ok": True}

    for method in ("chat_startStream", "chat_appendStream", "chat_stopStream"):
        monkeypatch.setattr(AsyncWebClient, method, ok)
    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", post)
    stream = SlackStreamSurface(credentials={"access_token": "xoxb-test"})
    opened = await stream.append_stream_text(_slack_event(), None, "")
    body = "\n\n".join(["word " * 2000] * 4)

    finished = await stream.finish_progress(_slack_event(), opened.handle, body)

    # Delivered: the caller must not delete the stream and send it all again.
    assert finished is True
    # Part one went, part two failed permanently and was not retried, and the
    # parts after the gap were not sent out of order.
    assert len(posts) == 2


# ── held files are released only with the reply that carried them ───────────


@pytest.mark.parametrize(
    ("files", "released"),
    [(PartDelivery.NATIVE, True), (PartDelivery.UNDELIVERED, False)],
    ids=["files-arrived", "files-lost"],
)
async def test_held_files_stay_held_when_their_part_did_not_arrive(
    claims, files, released
):
    async def receipt(*_: Any, **__: Any) -> DeliveryReceipt:
        return DeliveryReceipt(parts={"text": PartDelivery.NATIVE, "files": files})

    conversation_id, run = uuid4(), RunFiles(uuid4())
    assert await pending_envelope.remember_display_path(
        conversation_id, run, "/me/report.pdf"
    )
    egress = egress_service.SurfaceEgress(
        uow=SimpleNamespace(session=SimpleNamespace(info={})),  # type: ignore[arg-type]
        delivery=SimpleNamespace(deliver_envelope_receipt=receipt),  # type: ignore[arg-type]
        progress=SimpleNamespace(),  # type: ignore[arg-type]
    )

    delivered = await egress._deliver_with_held_files(
        SimpleNamespace(),  # type: ignore[arg-type]
        envelope=SurfaceEnvelope(text="hi"),
        metadata={},
        conversation_id=conversation_id,
        run=run,
        held=["/me/report.pdf"],
    )

    assert delivered is not None
    still_held = await pending_envelope.held_display_paths(conversation_id, run)
    assert still_held == ([] if released else ["/me/report.pdf"])
