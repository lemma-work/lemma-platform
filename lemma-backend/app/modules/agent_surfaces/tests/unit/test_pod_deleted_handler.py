"""Tests for the agent_surfaces pod-deletion cleanup handler."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.events import (
    SurfaceConnectedEvent,
    SurfaceMessageAnsweredEvent,
    SurfaceWebhookReceivedEvent,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.events import handlers
from app.modules.test_support.fakes import PassthroughEventInbox


@asynccontextmanager
async def _mock_uow_factory(uow_mock):
    yield uow_mock


def _webhook_envelope(**kwargs) -> dict:
    """A webhook event as it actually arrives: a plain dict off the stream.

    The handler takes ``dict`` and filters on ``event_type`` because
    ``surface_events`` is shared with the analytics projections. Building the
    model here and dumping it keeps the test honest about the wire shape while
    still failing if the event's own fields change.
    """
    return SurfaceWebhookReceivedEvent(**kwargs).model_dump(mode="json")


@pytest.mark.asyncio
async def test_on_pod_deleted_removes_pod_surfaces(monkeypatch):
    service = AsyncMock()
    service.delete_all_surfaces_for_pod.return_value = 2
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "get_surface_service", lambda uow: service)

    pod_id = uuid4()
    event = {
        "event_type": "pod.deleted",
        "pod_id": str(pod_id),
        "organization_id": str(uuid4()),
    }

    await handlers.on_pod_deleted(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        inbox=PassthroughEventInbox(),
    )

    service.delete_all_surfaces_for_pod.assert_awaited_once_with(pod_id)


@pytest.mark.asyncio
async def test_on_pod_deleted_ignores_non_delete_events(monkeypatch):
    service = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "get_surface_service", lambda uow: service)

    event = {
        "event_type": "pod.member.removed",
        "pod_id": str(uuid4()),
        "user_id": str(uuid4()),
    }

    await handlers.on_pod_deleted(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        inbox=PassthroughEventInbox(),
    )

    service.delete_all_surfaces_for_pod.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_surface_webhook_enqueues_prepared_context(monkeypatch):
    handler = AsyncMock()
    # Sync on the real service: the delivery is split before anything is
    # awaited, and every non-batching platform hands the request back.
    handler.split_webhook_deliveries = lambda request: [request]
    context = _reply_context()
    handler.try_handle_channel_setup.return_value = False
    handler.try_handle_lifecycle.return_value = False
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = context
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        _webhook_envelope(source="telegram", payload={"update_id": 1}),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.try_handle_interaction.assert_awaited_once()
    handler.prepare_ingress.assert_awaited_once()
    job_queue.enqueue.assert_awaited_once()
    assert job_queue.enqueue.await_args.kwargs["payload"]["context"]["mode"] == "reply"


@pytest.mark.asyncio
async def test_a_batched_delivery_enqueues_one_job_per_message(monkeypatch):
    """WhatsApp may put several messages in one delivery; the parser reads the
    first. Each part now becomes its own job, and only the first keeps the bare
    dedup id so an ordinary single-message delivery keys exactly as before."""
    handler = AsyncMock()
    handler.try_handle_channel_setup.return_value = False
    handler.try_handle_lifecycle.return_value = False
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = _reply_context()
    handler.split_webhook_deliveries = lambda request: [request, request, request]
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    envelope = _webhook_envelope(source="whatsapp", payload={"entry": []})
    event_id = envelope["event_id"]
    await handlers.handle_surface_webhook(
        envelope,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.enqueue.await_count == 3
    job_ids = [call.kwargs["_job_id"] for call in job_queue.enqueue.await_args_list]
    assert job_ids == [
        f"surface-event:{event_id}",
        f"surface-event:{event_id}:1",
        f"surface-event:{event_id}:2",
    ]


@pytest.mark.asyncio
async def test_handle_surface_webhook_skips_queue_when_interaction_was_handled(
    monkeypatch,
):
    handler = AsyncMock()
    # Sync on the real service: the delivery is split before anything is
    # awaited, and every non-batching platform hands the request back.
    handler.split_webhook_deliveries = lambda request: [request]
    handler.try_handle_channel_setup.return_value = False
    handler.try_handle_lifecycle.return_value = False
    handler.try_handle_interaction.return_value = True
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        _webhook_envelope(source="telegram", payload={"callback_query": {}}),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.prepare_ingress.assert_not_awaited()
    job_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_surface_webhook_skips_queue_when_no_context(monkeypatch):
    handler = AsyncMock()
    # Sync on the real service: the delivery is split before anything is
    # awaited, and every non-batching platform hands the request back.
    handler.split_webhook_deliveries = lambda request: [request]
    handler.try_handle_channel_setup.return_value = False
    handler.try_handle_lifecycle.return_value = False
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = None
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        _webhook_envelope(source="telegram", payload={"update_id": 2}),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.prepare_ingress.assert_awaited_once()
    job_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_webhook_builds_direct_ingress(monkeypatch):
    handler = AsyncMock()
    # Sync on the real service: the delivery is split before anything is
    # awaited, and every non-batching platform hands the request back.
    handler.split_webhook_deliveries = lambda request: [request]
    handler.try_handle_channel_setup.return_value = False
    handler.try_handle_lifecycle.return_value = False
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = None
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)
    surface_id = uuid4()

    await handlers.handle_surface_webhook(
        _webhook_envelope(
            source="telegram",
            surface_id=surface_id,
            payload={"update_id": 3},
            headers={"x-provider": "telegram"},
        ),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=AsyncMock(),
        inbox=PassthroughEventInbox(),
    )

    request = handler.prepare_ingress.await_args.args[0]
    assert isinstance(request, handlers.SurfaceDirectWebhookIngress)
    assert request.surface_id == surface_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "envelope",
    [
        pytest.param(
            SurfaceConnectedEvent(
                surface_id=uuid4(), pod_id=uuid4(), platform="RESEND"
            ).model_dump(mode="json"),
            id="surface.connected",
        ),
        pytest.param(
            SurfaceMessageAnsweredEvent(surface_id=uuid4(), pod_id=uuid4()).model_dump(
                mode="json"
            ),
            id="surface.message.answered",
        ),
    ],
)
async def test_handle_surface_webhook_ignores_the_other_events_on_its_stream(
    monkeypatch, envelope
):
    """The analytics projections share ``surface_events``. They must not poison it.

    This handler used to annotate its parameter as
    ``SurfaceWebhookReceivedEvent``, which made fast-depends validate — and
    fail — before the acknowledgement. An unackable message stays in the
    pending-entries list and the reclaim subscriber hands it back every 60
    seconds, forever. In development that ran at ~119 redeliveries an hour off
    two stuck messages, and it grew by one permanently-stuck message per agent
    created, because every agent is given an auto-provisioned Resend mailbox
    whose creation publishes ``surface.connected``.

    ``RESEND`` is the platform on purpose: it is the one that actually happened.
    """
    handler = AsyncMock()
    # Sync on the real service: the delivery is split before anything is
    # awaited, and every non-batching platform hands the request back.
    handler.split_webhook_deliveries = lambda request: [request]
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    # Returning cleanly is the whole assertion: FastStream acknowledges only a
    # handler that does not raise, and the ack is what lets the message leave
    # the PEL. Anything escaping here is the poison loop reopening.
    await handlers.handle_surface_webhook(
        envelope,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.try_handle_channel_setup.assert_not_awaited()
    handler.prepare_ingress.assert_not_awaited()
    job_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_surface_message_uses_worker_factory(monkeypatch):
    service = AsyncMock()
    worker_ctx = SimpleNamespace(
        build_surface_event_handler_with_factory=Mock(return_value=service)
    )
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=worker_ctx))
    registered_task = handlers.process_surface_message
    monkeypatch.setattr(
        handlers,
        "process_surface_message",
        SimpleNamespace(context=SimpleNamespace(task_id="surface-task-1")),
    )
    payload = handlers.SurfaceProcessMessageTaskPayload(
        context=_reply_context()
    ).model_dump(mode="json")

    await registered_task.fn(payload)

    worker_ctx.build_surface_event_handler_with_factory.assert_called_once()
    service.execute_chat.assert_awaited_once()


def _reply_context() -> SurfaceReplyContext:
    return SurfaceReplyContext(
        platform=SurfacePlatform.TELEGRAM,
        event=ParsedInboundSurfaceEvent(
            platform=SurfacePlatform.TELEGRAM,
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="123",
            sender_external_user_id="123",
            message_text="hi",
            is_dm=True,
            reply_target={"chat_id": "123"},
        ),
        reply_message="hello",
    )


@pytest.mark.asyncio
async def test_handle_surface_webhook_stops_at_a_lifecycle_event(monkeypatch):
    """A lifecycle event is answered and stopped — it starts no conversation.

    The bot joining a channel must not fall through to the message path, or an
    event with no message text would try to become a run.
    """
    handler = AsyncMock()
    # Sync on the real service: the delivery is split before anything is
    # awaited, and every non-batching platform hands the request back.
    handler.split_webhook_deliveries = lambda request: [request]
    handler.try_handle_channel_setup.return_value = False
    handler.try_handle_lifecycle.return_value = True
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        _webhook_envelope(source="slack", payload={"type": "event_callback"}),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.try_handle_lifecycle.assert_awaited_once()
    handler.try_handle_interaction.assert_not_awaited()
    handler.prepare_ingress.assert_not_awaited()
    job_queue.enqueue.assert_not_awaited()
