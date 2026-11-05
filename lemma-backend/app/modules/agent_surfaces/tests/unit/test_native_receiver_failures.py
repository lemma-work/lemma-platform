"""A native receiver that keeps failing says so, at a level production keeps.

Pollers and socket receivers logged every failure at debug, which
`LOG_LEVEL=INFO` drops before formatting: a revoked bot token looped every five
seconds forever, and it looked like a bot that had simply gone quiet. A
credential upstream refuses stands the receiver's work item down for the
cooldown (``Defer``), not a crash loop.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.core.infrastructure.work import Defer
from app.modules.agent_surfaces.events.receiver_work import (
    CONFLICT_COOLDOWN,
    supervise,
)
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    NativeReceiverCredentialsRejected,
    ReceiverFailureStreak,
)
from app.modules.agent_surfaces.services.resend_polling_receiver import (
    ResendPollingReceiverRunner,
)
from app.modules.agent_surfaces.services.telegram_polling_runner import (
    TelegramPollingReceiverRunner,
)

pytestmark = pytest.mark.asyncio


class _Inbox:
    """The receiver inbox, in memory: what was stored, and the cursor kept."""

    def __init__(self) -> None:
        self.cursors: list[str | None] = []
        self.accepted: list[dict] = []
        self.load_cursor = AsyncMock(return_value=None)

    async def store_cursor(self, cursor):
        self.cursors.append(cursor)

    async def accept(self, **kwargs):
        self.accepted.append(kwargs)
        self.cursors.append(kwargs.get("cursor"))


def _telegram_runner() -> TelegramPollingReceiverRunner:
    return TelegramPollingReceiverRunner(
        NativeReceiverCandidate(
            key="telegram:system:abc",
            platform=SurfacePlatform.TELEGRAM,
            surface_ids=(),
            credential_label="system",
            credentials={"bot_token": "token-1"},
        ),
        _Inbox(),  # type: ignore[arg-type]
    )


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://telegram.example.test/getUpdates")
    return httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(status, request=request)
    )


@pytest.mark.parametrize("status", [401, 404])
async def test_a_revoked_telegram_token_stands_down_instead_of_looping(status):
    runner = _telegram_runner()
    calls: list[str] = []

    async def fake_telegram_api(client, base_url, method, params):
        calls.append(method)
        if method == "deleteWebhook":
            return {"ok": True}
        raise _http_error(status)

    runner._telegram_api = fake_telegram_api  # type: ignore[method-assign]
    with pytest.raises(NativeReceiverCredentialsRejected):
        await runner.run()

    assert calls == ["deleteWebhook", "getUpdates"]


async def test_a_telegram_poller_that_keeps_failing_warns_once_per_streak(
    monkeypatch, caplog
):
    runner = _telegram_runner()
    polls: list[int] = []

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def fake_telegram_api(client, base_url, method, params):
        if method == "deleteWebhook":
            return {"ok": True}
        polls.append(1)
        if len(polls) > 3:
            raise asyncio.CancelledError
        raise _http_error(500)

    runner._telegram_api = fake_telegram_api  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await runner.run()

    failures = [r for r in caplog.records if "poll_failed" in r.getMessage()]
    assert len(failures) == 1, [r.getMessage() for r in caplog.records]
    assert failures[0].levelno == logging.WARNING


async def test_a_failure_streak_reminds_after_its_interval_and_resets_on_success(
    caplog,
):
    now = [0.0]
    streak = ReceiverFailureStreak(
        platform=SurfacePlatform.RESEND,
        credential_label="system",
        repeat_after_seconds=60,
        clock=lambda: now[0],
    )
    with caplog.at_level(logging.INFO):
        streak.failed(RuntimeError("down"))
        now[0] = 30
        streak.failed(RuntimeError("down"))
        now[0] = 61
        streak.failed(RuntimeError("down"))
        streak.succeeded()
        streak.failed(RuntimeError("down again"))

    messages = [r.getMessage() for r in caplog.records]
    assert sum("poll_failed" in m for m in messages) == 3
    assert sum("poll_recovered" in m for m in messages) == 1
    assert streak.failures == 1


class _FakeResendService:
    def __init__(self, page: list[dict[str, str]]) -> None:
        self._page = page

    async def list_received_emails(self, *, after=None, limit=20):
        return {"data": list(self._page), "has_more": False}


def _resend_runner(ingest, inbox: _Inbox) -> ResendPollingReceiverRunner:
    return ResendPollingReceiverRunner(
        NativeReceiverCandidate(
            key="resend:system:abc",
            platform=SurfacePlatform.RESEND,
            surface_ids=(),
            credential_label="system",
            credentials={"api_key": "re_test"},
        ),
        inbox,  # type: ignore[arg-type]
        ingest=ingest,
    )


async def test_an_email_that_could_not_be_stored_holds_the_cursor_and_is_retried(
    caplog,
):
    """Storing is a row insert: a failure there is retried, never skipped.

    Whatever goes wrong *processing* an email is the ingest item's to retry and
    record; it no longer holds the cursor, so there is no poison counter.
    """
    inbox = _Inbox()
    stored: list[str] = []
    failing = {"middle"}

    async def ingest(item):
        if item["id"] in failing:
            raise ConnectionError("database blip")
        stored.append(item["id"])
        await inbox.store_cursor(item["id"])

    runner = _resend_runner(ingest, inbox)
    # Newest first, as Resend lists them.
    service = _FakeResendService([{"id": "after"}, {"id": "middle"}, {"id": "before"}])

    with caplog.at_level(logging.WARNING):
        cursor = await runner.poll_once(service, "seed")
    assert cursor == "before"
    assert inbox.cursors[-1] == "before"
    assert "email_ingest_failed" in caplog.text

    failing.clear()
    cursor = await runner.poll_once(service, cursor)
    assert cursor == "after"
    assert stored == ["before", "middle", "after"], "nothing re-read, nothing skipped"


async def test_a_rejected_credential_stands_the_receiver_down_with_a_warning(caplog):
    async def rejected() -> None:
        raise NativeReceiverCredentialsRejected("token revoked")

    ctx = SimpleNamespace(until_stopping=lambda: asyncio.sleep(3600))

    async def wanted() -> bool:
        return True

    with caplog.at_level(logging.WARNING), pytest.raises(Defer) as deferred:
        await supervise(
            ctx,  # type: ignore[arg-type]
            rejected(),
            still_wanted=wanted,
            label="system",
            platform="TELEGRAM",
        )

    assert deferred.value.delay == CONFLICT_COOLDOWN
    assert "native_receiver_credentials_rejected" in caplog.text
