from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from unittest.mock import AsyncMock
from uuid import UUID

from app.modules.agent_surfaces.services import (
    event_receiver_service,
    resend_polling_receiver,
)
from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.telegram.client import normalize_bot_base_url
from app.modules.agent_surfaces.platforms.telegram.update_batching import (
    assemble_telegram_updates as _assemble_telegram_updates,
)
from app.modules.agent_surfaces.services import native_receiver_base
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverConflict,
)
from app.modules.agent_surfaces.services import telegram_polling_runner
from app.modules.agent_surfaces.services.telegram_polling_runner import (
    TelegramPollingReceiverRunner,
)
from app.modules.agent_surfaces.services.event_receiver_service import (
    NativeReceiverCandidate,
    NativeSurfaceReceiverCoordinator,
    ResendPollingReceiverRunner,
    _candidate_from_surface,
    _conflict_key,
    _lease_key,
    _publish_native_receiver_event,
    _receiver_key,
)
from app.modules.agent_surfaces.tests.unit.test_surface_service import _surface_entity


def test_normalize_telegram_base_url_appends_bot_token():
    assert (
        normalize_bot_base_url("https://api.telegram.org/bot", "token-1")
        == "https://api.telegram.org/bottoken-1"
    )
    assert (
        normalize_bot_base_url("https://api.telegram.org/bottoken-1", "token-1")
        == "https://api.telegram.org/bottoken-1"
    )


def test_slack_candidate_uses_app_token_and_account_scoped_key():
    account_id = UUID("019eadff-0000-7000-8000-000000000001")
    surface = _surface_entity(
        surface_type=SurfacePlatform.SLACK,
        account_id=account_id,
        credential_mode=SurfaceCredentialMode.CUSTOM,
    )
    candidate = _candidate_from_surface(
        surface,
        {
            "app_token": "xapp-custom",
            "bot_token": "xoxb-workspace",
        },
    )

    assert isinstance(candidate, NativeReceiverCandidate)
    assert candidate.platform is SurfacePlatform.SLACK
    assert candidate.credential_label == str(account_id)
    assert candidate.key.startswith(f"slack:{account_id}:")


def test_telegram_update_assembly_coalesces_same_sender_burst():
    updates = [
        {
            "update_id": 10,
            "message": {
                "message_id": 1,
                "date": 100,
                "chat": {"id": 20},
                "from": {"id": 30},
                "text": "first",
            },
        },
        {
            "update_id": 11,
            "message": {
                "message_id": 2,
                "date": 101,
                "chat": {"id": 20},
                "from": {"id": 30},
                "text": "second",
            },
        },
    ]

    assembled = _assemble_telegram_updates(updates)

    assert len(assembled) == 1
    assert assembled[0]["update_id"] == 11
    assert [message["text"] for message in assembled[0]["_lemma_batch_messages"]] == [
        "first",
        "second",
    ]


def test_telegram_update_assembly_keeps_different_chats_separate():
    updates = [
        {
            "update_id": 10,
            "message": {
                "message_id": 1,
                "date": 100,
                "chat": {"id": 20},
                "from": {"id": 30},
            },
        },
        {
            "update_id": 11,
            "message": {
                "message_id": 2,
                "date": 100,
                "chat": {"id": 21},
                "from": {"id": 30},
            },
        },
    ]

    assert len(_assemble_telegram_updates(updates)) == 2


@pytest.mark.asyncio
async def test_coordinator_stop_signals_before_run_loop_releases_redis():
    coordinator = NativeSurfaceReceiverCoordinator(
        uow_factory=lambda: None,
        scan_interval_seconds=1,
        redis_url="redis://unused",
    )
    redis_client = AsyncMock()
    coordinator._redis = redis_client

    await coordinator.stop()

    assert coordinator._stopping is True
    assert coordinator._wakeup.is_set()
    assert coordinator._redis is redis_client
    redis_client.aclose.assert_not_awaited()

    await coordinator._shutdown()
    assert coordinator._redis is None
    # Shared client: the coordinator releases it rather than closing the pool.
    redis_client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_polling_retries_transient_conflict_after_resetting_webhook(
    monkeypatch,
):
    runner = TelegramPollingReceiverRunner(
        NativeReceiverCandidate(
            key=_receiver_key("telegram", "system", "token"),
            platform=SurfacePlatform.TELEGRAM,
            surface_ids=(),
            credential_label="system",
            credentials={"bot_token": "token"},
        )
    )
    calls: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(event_receiver_service.asyncio, "sleep", fake_sleep)

    async def fake_telegram_api(client, base_url, method, params):
        calls.append(method)
        if method == "deleteWebhook":
            assert params == {"drop_pending_updates": False}
            return {"ok": True}
        if calls.count("getUpdates") > 1:
            raise asyncio.CancelledError
        request = httpx.Request("POST", f"{base_url}/{method}")
        response = httpx.Response(409, request=request)
        raise httpx.HTTPStatusError("conflict", request=request, response=response)

    runner._telegram_api = fake_telegram_api  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await runner.run()

    assert calls == ["deleteWebhook", "getUpdates", "getUpdates"]
    assert sleeps == [5]


@pytest.mark.asyncio
async def test_publish_native_receiver_event_emits_surface_webhook_event(monkeypatch):
    published = []

    async def publish(stream, event):
        published.append((stream, event))

    monkeypatch.setattr(
        native_receiver_base.EventPublisher,
        "publish",
        publish,
    )

    await _publish_native_receiver_event(
        source="telegram",
        payload={"update_id": 123},
        receiver_key=None,
    )

    assert len(published) == 1
    stream, event = published[0]
    assert stream == "surface_events"
    assert event.source == "telegram"
    assert event.payload == {"update_id": 123}
    assert event.headers == {"x-lemma-surface-event-mode": "native_receiver"}


@pytest.mark.asyncio
async def test_two_polled_bots_sharing_an_update_id_are_two_events(monkeypatch):
    """``update_id`` counts per bot, so the receiver key has to be in the id.

    Both bots are polled by the same worker and both are on their first update.
    One identity for the pair means the durable inbox claims a single row: the
    first person is answered, the second is dropped as a duplicate.
    """
    published = []

    async def publish(stream, event):
        published.append(event)

    monkeypatch.setattr(native_receiver_base.EventPublisher, "publish", publish)

    for key in ("telegram:one:aaa", "telegram:two:bbb"):
        await _publish_native_receiver_event(
            source="telegram", payload={"update_id": 1}, receiver_key=key
        )

    assert published[0].source_event_id != published[1].source_event_id
    assert published[0].event_id != published[1].event_id


def _resend_candidate() -> NativeReceiverCandidate:
    return NativeReceiverCandidate(
        key="resend:system:abc",
        platform=SurfacePlatform.RESEND,
        surface_ids=(),
        credential_label="system",
        credentials={"api_key": "re_test"},
    )


class _FakeResendService:
    """Serves list pages in order; records the ``after`` cursor of each call."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = list(pages)
        self.after_args: list[str | None] = []

    async def list_received_emails(self, *, after=None, limit=20):
        self.after_args.append(after)
        return self._pages.pop(0) if self._pages else {"data": [], "has_more": False}


def test_resend_candidate_is_keyed_by_the_system_key():
    surface = _surface_entity(surface_type=SurfacePlatform.RESEND, account_id=None)

    candidate = _candidate_from_surface(surface, {"api_key": "re_live_x"})

    assert isinstance(candidate, NativeReceiverCandidate)
    assert candidate.platform is SurfacePlatform.RESEND
    assert candidate.credential_label == "system"
    assert candidate.key.startswith("resend:system:")


@pytest.mark.asyncio
async def test_resend_first_poll_seeds_cursor_without_replaying_history():
    runner = ResendPollingReceiverRunner(_resend_candidate())
    service = _FakeResendService(
        [{"data": [{"id": "e3"}, {"id": "e2"}, {"id": "e1"}], "has_more": True}]
    )

    new_items, newest = await runner._collect_new_emails(service, cursor=None)

    assert newest == "e3"
    assert new_items == []  # history is seeded, not replayed
    assert service.after_args == [None]


@pytest.mark.asyncio
async def test_resend_poll_collects_only_emails_newer_than_cursor():
    runner = ResendPollingReceiverRunner(_resend_candidate())
    service = _FakeResendService(
        [
            {
                "data": [{"id": "e4"}, {"id": "e3"}, {"id": "e2"}, {"id": "e1"}],
                "has_more": True,
            }
        ]
    )

    new_items, newest = await runner._collect_new_emails(service, cursor="e2")

    assert newest == "e4"
    assert [item["id"] for item in new_items] == ["e4", "e3"]


@pytest.mark.asyncio
async def test_resend_ingest_resolves_surface_by_address_and_publishes(monkeypatch):
    published = []

    async def publish(stream, event):
        published.append(event)

    monkeypatch.setattr(resend_polling_receiver.EventPublisher, "publish", publish)

    surface = _surface_entity(surface_type=SurfacePlatform.RESEND, account_id=None)

    class _FakeRepo:
        def __init__(self, uow):
            pass

        async def get_active_by_address(self, *, platform, address):
            return surface if address == "pod-abc@mail.example.com" else None

    monkeypatch.setattr(resend_polling_receiver, "SurfaceRepository", _FakeRepo)

    class _FakeUow:
        session = object()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        resend_polling_receiver,
        "SessionUnitOfWorkFactory",
        lambda *a, **k: lambda: _FakeUow(),
    )

    runner = ResendPollingReceiverRunner(_resend_candidate())
    await runner._ingest_email(
        {
            "id": "email-1",
            "from": "person@example.com",
            "to": ["pod-abc@mail.example.com"],
            "subject": "Re: hi",
        }
    )

    assert len(published) == 1
    event = published[0]
    assert event.source == "resend"
    assert event.surface_id == surface.id
    # The same id the webhook mints for this email, so a deployment running
    # both routes deduplicates in the durable inbox rather than in a Redis key
    # with a 15-minute TTL.
    assert event.source_event_id == f"resend:{surface.id}:email-1"
    assert event.payload["to"] == "pod-abc@mail.example.com"


@pytest.mark.asyncio
async def test_resend_ingest_skips_when_no_surface_matches(monkeypatch):
    published = []

    async def publish(stream, event):
        published.append(event)

    monkeypatch.setattr(resend_polling_receiver.EventPublisher, "publish", publish)

    class _FakeRepo:
        def __init__(self, uow):
            pass

        async def get_active_by_address(self, *, platform, address):
            return None

    monkeypatch.setattr(resend_polling_receiver, "SurfaceRepository", _FakeRepo)

    class _FakeUow:
        session = object()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        resend_polling_receiver,
        "SessionUnitOfWorkFactory",
        lambda *a, **k: lambda: _FakeUow(),
    )

    runner = ResendPollingReceiverRunner(_resend_candidate())
    await runner._ingest_email(
        {"id": "x", "from": "a@b.com", "to": ["nobody@nowhere.com"]}
    )

    assert published == []


class _FakeRedis:
    """Enough of Redis for the lease and the stand-down mark."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def exists(self, key):
        return 1 if key in self.values else 0

    async def get(self, key):
        return self.values.get(key)

    async def expire(self, key, ttl):
        return key in self.values

    async def eval(self, script, numkeys, key, owner):
        if self.values.get(key) == owner:
            self.values.pop(key, None)
        return 1


def _telegram_candidate() -> NativeReceiverCandidate:
    return NativeReceiverCandidate(
        key=_receiver_key("telegram", "system", "shared-token"),
        platform=SurfacePlatform.TELEGRAM,
        surface_ids=(),
        credential_label="system",
        credentials={"bot_token": "shared-token"},
    )


@pytest.mark.asyncio
async def test_telegram_polling_names_the_owner_of_the_bot_instead_of_ending_quietly(
    monkeypatch,
):
    """A 409 that outlasts a handover is somebody else's bot, and says so.

    It used to `return` -- indistinguishable from a receiver that had finished
    its work -- so the coordinator's next scan started it again.
    """
    monkeypatch.setattr(telegram_polling_runner, "_TELEGRAM_CONFLICT_GRACE_SECONDS", 0)
    runner = TelegramPollingReceiverRunner(_telegram_candidate())

    async def fake_telegram_api(client, base_url, method, params):
        if method == "deleteWebhook":
            return {"ok": True}
        request = httpx.Request("POST", f"{base_url}/{method}")
        raise httpx.HTTPStatusError(
            "conflict", request=request, response=httpx.Response(409, request=request)
        )

    runner._telegram_api = fake_telegram_api  # type: ignore[method-assign]
    with pytest.raises(NativeReceiverConflict):
        await runner.run()


def _conflicted_coordinator(monkeypatch, starts: list[int]):
    class _ConflictingRunner:
        def __init__(self, candidate):
            self._candidate = candidate

        async def run(self):
            starts.append(1)
            raise NativeReceiverConflict("another consumer holds this bot")

    coordinator = NativeSurfaceReceiverCoordinator(
        uow_factory=lambda: None,
        scan_interval_seconds=1,
        redis_url="redis://unused",
        runner_factories={SurfacePlatform.TELEGRAM: _ConflictingRunner},
    )
    coordinator._redis = _FakeRedis()
    candidate = _telegram_candidate()
    monkeypatch.setattr(
        coordinator, "_load_candidates", AsyncMock(return_value=[candidate])
    )
    return coordinator, candidate


async def _reconcile_and_settle(coordinator, key):
    await coordinator.reconcile()
    task = coordinator._tasks.get(key)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_receiver_refused_by_upstream_stands_down_instead_of_restarting(
    monkeypatch, caplog
):
    """The Desktop-plus-Cloud case: one bot token, two deployments, two Redises.

    The lease only orders workers within one deployment, so both sides used to
    take their own lease and poll. The loser gave up after 75 seconds, was
    started again on the next scan, and spent the next 75 seconds taking
    updates away from the side that was working -- for as long as the bot was
    configured in both places, and only ever at debug level.
    """
    starts: list[int] = []
    coordinator, candidate = _conflicted_coordinator(monkeypatch, starts)

    with caplog.at_level(logging.ERROR):
        await _reconcile_and_settle(coordinator, candidate.key)

    # A handled misconfiguration is not a crashed background task. Letting the
    # conflict out of the task would have `create_background_task` report it as
    # one, with a traceback, once per stand-down.
    assert "background_task.failed" not in caplog.text

    redis = coordinator._redis
    assert _conflict_key(candidate.key) in redis.values
    assert _lease_key(candidate.key) not in redis.values, (
        "the lease has to be released, or the deployment blocks itself too"
    )

    await coordinator.reconcile()
    await coordinator.reconcile()

    assert starts == [1], "standing down means standing down, not retrying every scan"
    assert candidate.key not in coordinator._tasks


@pytest.mark.asyncio
async def test_the_stand_down_expires_so_the_bot_comes_back_on_its_own(monkeypatch):
    """Nothing else has to be cleared for the takeover to happen.

    If the other consumer is switched off, the mark expiring is the whole of
    what stands between this side and the bot.
    """
    starts: list[int] = []
    coordinator, candidate = _conflicted_coordinator(monkeypatch, starts)
    await _reconcile_and_settle(coordinator, candidate.key)

    # What the TTL does, done by hand: nothing here waits ten minutes.
    coordinator._redis.values.pop(_conflict_key(candidate.key))

    await _reconcile_and_settle(coordinator, candidate.key)
    assert starts == [1, 1]
