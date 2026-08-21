from __future__ import annotations

import asyncio

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
from app.modules.agent_surfaces.services.event_receiver_service import (
    NativeReceiverCandidate,
    NativeSurfaceReceiverCoordinator,
    ResendPollingReceiverRunner,
    TelegramPollingReceiverRunner,
    _assemble_telegram_updates,
    _candidate_from_surface,
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
        event_receiver_service.EventPublisher,
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
    assert event.source_event_id == "resend:native:email-1"
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
