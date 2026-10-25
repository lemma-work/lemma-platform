"""Native receivers: what they read, how they key it, and where they keep their place.

The receivers run as ``surface.receiver`` work items now (``events/receiver_work``);
these are the runners' own behaviours, with the inbox they store into faked.
The lease, failover and cursor durability are exercised against Postgres in
``test_surface_receivers_e2e``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.telegram.client import normalize_bot_base_url
from app.modules.agent_surfaces.platforms.telegram.update_batching import (
    assemble_telegram_updates as _assemble_telegram_updates,
)
from app.modules.agent_surfaces.services import (
    resend_polling_receiver,
    telegram_polling_runner,
)
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    NativeReceiverConflict,
    receiver_key as _receiver_key,
)
from app.modules.agent_surfaces.services.native_receiver_candidates import (
    candidate_from_surface as _candidate_from_surface,
)
from app.modules.agent_surfaces.domain.source_event_ids import native_source_event_id
from app.modules.agent_surfaces.services.resend_polling_receiver import (
    ResendPollingReceiverRunner,
)
from app.modules.agent_surfaces.services.telegram_polling_runner import (
    TelegramPollingReceiverRunner,
)
from app.modules.agent_surfaces.tests.unit.test_surface_service import _surface_entity


class _Inbox:
    """The receiver inbox, in memory."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.accepted: list[dict] = []
        self.cursors: list[str | None] = []
        self.load_cursor = AsyncMock(return_value=None)
        self._fail = fail

    async def store_cursor(self, cursor):
        self.cursors.append(cursor)

    async def accept(self, **kwargs):
        if self._fail is not None:
            raise self._fail
        self.accepted.append(kwargs)
        if kwargs.get("cursor") is not None:
            self.cursors.append(kwargs["cursor"])


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
        pod_connector_slug="slack-bot",
        credential_mode=SurfaceCredentialMode.CUSTOM,
    )
    candidate = _candidate_from_surface(
        surface,
        {
            "app_token": "xapp-custom",
            "bot_token": "xoxb-workspace",
        },
        account_id=account_id,
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
        ),
        _Inbox(),  # type: ignore[arg-type]
    )
    calls: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(telegram_polling_runner.asyncio, "sleep", fake_sleep)

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


def test_two_polled_bots_sharing_an_update_id_are_two_deliveries():
    """``update_id`` counts per bot, so the receiver key has to be in the id.

    One identity for the pair would make the inbound row's unique key read the
    second bot's first update as a duplicate of the first's.
    """
    first = native_source_event_id(
        "telegram", {"update_id": 1}, receiver_key="telegram:one:aaa"
    )
    second = native_source_event_id(
        "telegram", {"update_id": 1}, receiver_key="telegram:two:bbb"
    )
    assert first != second


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
    surface = _surface_entity(
        surface_type=SurfacePlatform.RESEND, pod_connector_slug=None
    )

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
async def test_resend_ingest_resolves_surface_by_address_and_stores_it(monkeypatch):
    surface = _surface_entity(
        surface_type=SurfacePlatform.RESEND, pod_connector_slug=None
    )

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
    inbox = _Inbox()
    runner = ResendPollingReceiverRunner(_resend_candidate(), inbox)  # type: ignore[arg-type]
    await runner._ingest_email(
        {
            "id": "email-1",
            "from": "person@example.com",
            "to": ["pod-abc@mail.example.com"],
            "subject": "Re: hi",
        }
    )

    [stored] = inbox.accepted
    assert stored["source"] == "resend"
    assert stored["surface_id"] == surface.id
    # The same id the webhook mints for this email, under the same receiver
    # scope, so a deployment running both routes stores it once.
    assert stored["source_event_id"] == f"resend:{surface.id}:email-1"
    assert stored["receiver_scope"] == str(surface.id)
    assert stored["cursor"] == "email-1"
    assert stored["payload"]["to"] == "pod-abc@mail.example.com"


@pytest.mark.asyncio
async def test_resend_ingest_moves_past_mail_for_no_surface(monkeypatch):
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
    inbox = _Inbox()
    runner = ResendPollingReceiverRunner(_resend_candidate(), inbox)  # type: ignore[arg-type]
    await runner._ingest_email(
        {"id": "x", "from": "a@b.com", "to": ["nobody@nowhere.com"]}
    )

    assert inbox.accepted == []
    assert inbox.cursors == ["x"]


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
    its work. Raising lets its work item stand down for the cooldown.
    """
    monkeypatch.setattr(telegram_polling_runner, "_TELEGRAM_CONFLICT_GRACE_SECONDS", 0)
    runner = TelegramPollingReceiverRunner(_telegram_candidate(), _Inbox())  # type: ignore[arg-type]

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


_SURFACE_ID = UUID("019eadff-0000-7000-8000-000000000001")


# The shared system bot polls with no surfaces of its own, and its offset is
# kept all the same: without it a restart re-reads every update Telegram holds.
@pytest.mark.parametrize("surface_ids", [(_SURFACE_ID,), ()])
@pytest.mark.asyncio
async def test_telegram_offset_moves_with_the_update_it_passed(surface_ids):
    """The update and the offset past it are one store, so a crash can neither
    skip the update nor -- the row is unique on it -- store it twice."""
    inbox = _Inbox()
    runner = TelegramPollingReceiverRunner(
        NativeReceiverCandidate(
            key="telegram:system:abc",
            platform=SurfacePlatform.TELEGRAM,
            surface_ids=surface_ids,
            credential_label="system",
            credentials={"bot_token": "token-1"},
        ),
        inbox,  # type: ignore[arg-type]
    )

    offset = await runner._dispatch({"update_id": 41, "message": {"text": "hi"}}, None)

    assert offset == 42
    [stored] = inbox.accepted
    assert stored["cursor"] == "42"
    assert stored["payload"]["update_id"] == 41


@pytest.mark.asyncio
async def test_a_failed_store_does_not_advance_the_offset():
    runner = TelegramPollingReceiverRunner(
        _telegram_candidate(),
        _Inbox(fail=ConnectionError("database blip")),  # type: ignore[arg-type]
    )

    with pytest.raises(ConnectionError):
        await runner._dispatch({"update_id": 41, "message": {"text": "hi"}}, 41)
