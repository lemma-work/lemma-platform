"""Deterministic guards for the unified Telegram client + delivery retry."""

from __future__ import annotations

import httpx
import pytest

from app.modules.agent_surfaces.platforms.delivery import (
    DeliveryClassification,
    RetryPolicy,
    with_retry,
)
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
    classify_telegram_error,
    file_api_url,
    normalize_bot_base_url,
    resolve_api_base,
    telegram_retry_after,
)


# --- URL helpers ----------------------------------------------------------


def test_resolve_api_base_prefers_credential_override():
    assert resolve_api_base({"api_base_url": "http://fake/bot"}) == "http://fake/bot"
    assert resolve_api_base({}) == "https://api.telegram.org/bot"
    assert resolve_api_base(None) == "https://api.telegram.org/bot"


def test_normalize_bot_base_url_folds_all_variants():
    assert normalize_bot_base_url("https://api.telegram.org/bot", "T") == (
        "https://api.telegram.org/botT"
    )
    assert normalize_bot_base_url("http://x/bot", "T") == "http://x/botT"
    assert normalize_bot_base_url("http://x", "T") == "http://x/botT"
    assert normalize_bot_base_url("http://x/botT", "T") == "http://x/botT"


def test_file_api_url_inserts_file_segment():
    assert file_api_url("https://api.telegram.org/bot", "T") == (
        "https://api.telegram.org/file/botT"
    )


# --- envelope parsing -----------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeHttpx:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url, json):
        self.calls.append((url, json))
        return self._response


@pytest.mark.asyncio
async def test_call_returns_result_on_ok():
    client = TelegramClient(bot_token="T", api_base="http://x/bot")
    fake = _FakeHttpx(_FakeResponse(200, {"ok": True, "result": {"message_id": 1}}))
    data = await client.call("sendMessage", {"chat_id": "1"}, client=fake)
    assert data["result"]["message_id"] == 1
    assert fake.calls[0][0] == "http://x/botT/sendMessage"


@pytest.mark.asyncio
async def test_call_raises_with_description_and_retry_after():
    client = TelegramClient(bot_token="T", api_base="http://x/bot")
    fake = _FakeHttpx(
        _FakeResponse(
            429,
            {
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 7},
            },
        )
    )
    with pytest.raises(TelegramApiError) as exc_info:
        await client.call("sendMessage", {}, client=fake)
    err = exc_info.value
    assert err.status_code == 429
    assert err.description == "Too Many Requests"
    assert err.retry_after == 7.0
    assert err.is_parse_entities_error is False


@pytest.mark.asyncio
async def test_call_flags_parse_entities_error():
    client = TelegramClient(bot_token="T", api_base="http://x/bot")
    fake = _FakeHttpx(
        _FakeResponse(
            400, {"ok": False, "description": "Bad Request: can't parse entities: x"}
        )
    )
    with pytest.raises(TelegramApiError) as exc_info:
        await client.call("sendMessage", {}, client=fake)
    assert exc_info.value.is_parse_entities_error is True


# --- classification -------------------------------------------------------


def test_classify_telegram_error():
    assert (
        classify_telegram_error(TelegramApiError(method="m", status_code=429))
        is DeliveryClassification.TRANSIENT
    )
    assert (
        classify_telegram_error(TelegramApiError(method="m", status_code=500))
        is DeliveryClassification.TRANSIENT
    )
    assert (
        classify_telegram_error(TelegramApiError(method="m", status_code=400))
        is DeliveryClassification.PERMANENT
    )
    assert (
        classify_telegram_error(httpx.ConnectError("boom"))
        is DeliveryClassification.TRANSIENT
    )
    assert classify_telegram_error(ValueError("x")) is DeliveryClassification.PERMANENT


def test_telegram_retry_after():
    assert (
        telegram_retry_after(
            TelegramApiError(method="m", status_code=429, retry_after=5)
        )
        == 5.0
    )
    assert telegram_retry_after(TelegramApiError(method="m", status_code=400)) is None


# --- with_retry -----------------------------------------------------------


@pytest.mark.asyncio
async def test_with_retry_retries_transient_then_succeeds():
    attempts = {"n": 0}
    sleeps: list[float] = []

    async def send():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TelegramApiError(method="m", status_code=500)
        return "ok"

    result = await with_retry(
        send,
        policy=RetryPolicy(max_attempts=5, base_delay=0.1),
        classify=classify_telegram_error,
        retry_after=telegram_retry_after,
        sleep=lambda d: sleeps.append(d) or _noop(),
    )
    assert result == "ok"
    assert attempts["n"] == 3
    assert sleeps == [0.1, 0.2]


@pytest.mark.asyncio
async def test_with_retry_raises_permanent_immediately():
    attempts = {"n": 0}

    async def send():
        attempts["n"] += 1
        raise TelegramApiError(method="m", status_code=400, description="bad")

    with pytest.raises(TelegramApiError):
        await with_retry(
            send,
            policy=RetryPolicy(max_attempts=5),
            classify=classify_telegram_error,
            sleep=lambda d: _noop(),
        )
    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_with_retry_honors_retry_after():
    sleeps: list[float] = []

    async def send():
        raise TelegramApiError(method="m", status_code=429, retry_after=3)

    with pytest.raises(TelegramApiError):
        await with_retry(
            send,
            policy=RetryPolicy(max_attempts=2, base_delay=0.1),
            classify=classify_telegram_error,
            retry_after=telegram_retry_after,
            sleep=lambda d: sleeps.append(d) or _noop(),
        )
    assert sleeps == [3.0]


async def _noop():
    return None
