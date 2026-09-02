"""A rate-limited answer is retried, not dropped -- on every platform.

Telegram and WhatsApp classify 429/5xx as transient and retry through
``platforms/delivery.with_retry``. Slack and Teams did neither: a ``ratelimited``
response was caught upstream, recorded as UNDELIVERED, and the answer never
arrived. That is worst on Slack, which limits ``chat.postMessage`` per channel
and is exactly where a long answer is posted as several messages in a row.
"""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.delivery import (
    DeliveryClassification,
    RetryPolicy,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    classify_slack_error,
    slack_retry_after,
)
from app.modules.agent_surfaces.platforms.teams.client import (
    classify_teams_error,
    teams_retry_after,
)

#: Retries with no wait, so a test is not paced by a real rate limit.
_IMMEDIATE = RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0)


class _SlackResponse:
    """The shape ``SlackApiError`` carries: a status and the response headers."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.data = {"ok": False}

    def __str__(self) -> str:
        return f"<SlackResponse status={self.status_code}>"


def _rate_limited(retry_after: str = "3") -> SlackApiError:
    return SlackApiError(
        "ratelimited", _SlackResponse(429, {"Retry-After": retry_after})
    )


def _slack_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="thread-1",
        message_text="hi",
        reply_target={"channel": "C1"},
    )


def _teams_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="tenant-1",
        external_thread_id="thread-1",
        message_text="hi",
        reply_target={
            "conversation_id": "19:conv",
            "service_url": "https://smba.example.test/teams",
        },
    )


# --- classification --------------------------------------------------------


def test_classify_slack_error():
    assert classify_slack_error(_rate_limited()) is DeliveryClassification.TRANSIENT
    assert (
        classify_slack_error(SlackApiError("server", _SlackResponse(503)))
        is DeliveryClassification.TRANSIENT
    )
    assert (
        classify_slack_error(SlackApiError("channel_not_found", _SlackResponse(400)))
        is DeliveryClassification.PERMANENT
    )
    assert (
        classify_slack_error(aiohttp.ClientConnectionError("boom"))
        is DeliveryClassification.TRANSIENT
    )
    assert classify_slack_error(ValueError("x")) is DeliveryClassification.PERMANENT


def test_slack_retry_after_reads_the_header_slack_sends():
    assert slack_retry_after(_rate_limited("7")) == 7.0
    assert slack_retry_after(SlackApiError("nope", _SlackResponse(400))) is None
    assert slack_retry_after(ValueError("x")) is None


def _teams_error(status: int, headers: dict[str, str] | None = None):
    return aiohttp.ClientResponseError(
        request_info=None,  # type: ignore[arg-type]
        history=(),
        status=status,
        message="",
        headers=headers,
    )


def test_classify_teams_error():
    assert classify_teams_error(_teams_error(429)) is DeliveryClassification.TRANSIENT
    assert classify_teams_error(_teams_error(502)) is DeliveryClassification.TRANSIENT
    assert classify_teams_error(_teams_error(403)) is DeliveryClassification.PERMANENT
    assert (
        classify_teams_error(aiohttp.ClientConnectionError("boom"))
        is DeliveryClassification.TRANSIENT
    )
    assert classify_teams_error(ValueError("x")) is DeliveryClassification.PERMANENT


def test_teams_retry_after_reads_the_header():
    assert teams_retry_after(_teams_error(429, {"Retry-After": "4"})) == 4.0
    assert teams_retry_after(_teams_error(429)) is None
    assert teams_retry_after(ValueError("x")) is None


# --- the send itself -------------------------------------------------------


def _slack_service(**credentials: Any):
    from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

    service = SlackPlatformService(
        credentials={"access_token": "xoxb-test", **credentials}
    )
    service._retry_policy = _IMMEDIATE
    return service


async def test_slack_retries_a_rate_limited_chunk_instead_of_dropping_it(monkeypatch):
    """The answer arrives on the retry rather than being recorded as lost."""
    attempts: list[dict[str, Any]] = []

    async def post(self, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise _rate_limited()
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", post)

    await _slack_service().send_message(event=_slack_event(), message="the answer")

    assert len(attempts) == 2
    assert attempts[-1]["channel"] == "C1"


async def test_slack_does_not_retry_a_permanent_failure(monkeypatch):
    """A missing channel is not a rate limit; retrying it just delays the error."""
    attempts: list[dict[str, Any]] = []

    async def post(self, **kwargs):
        attempts.append(kwargs)
        raise SlackApiError("channel_not_found", _SlackResponse(400))

    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", post)

    with pytest.raises(SlackApiError):
        await _slack_service().send_message(event=_slack_event(), message="the answer")

    assert len(attempts) == 1


async def test_teams_retries_a_rate_limited_reply(monkeypatch):
    from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter

    posts: list[str] = []

    class _Response:
        def __init__(self, status: int) -> None:
            self.status = status

        def raise_for_status(self) -> None:
            if self.status >= 400:
                raise _teams_error(self.status, {"Retry-After": "0"})

        async def json(self) -> dict[str, Any]:
            return {"id": "activity-1"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info) -> None:
            return None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info) -> None:
            return None

        def post(self, url, **kwargs):
            posts.append(url)
            return _Response(429 if len(posts) == 1 else 200)

    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kwargs: _Session())

    adapter = TeamsSurfaceAdapter()
    adapter._retry_policy = _IMMEDIATE
    monkeypatch.setattr(
        adapter, "_get_bot_token", lambda tenant_id: _resolved("bot-token")
    )

    await adapter.send_message(
        credentials={}, event=_teams_event(), message="the answer"
    )

    assert len(posts) == 2


async def _resolved(value: str) -> str:
    return value
