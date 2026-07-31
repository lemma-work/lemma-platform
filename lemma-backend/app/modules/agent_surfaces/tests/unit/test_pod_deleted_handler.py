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
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.events import handlers
from app.modules.test_support.fakes import PassthroughEventInbox
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import ScheduleType


@asynccontextmanager
async def _mock_uow_factory(uow_mock):
    yield uow_mock


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
    context = _reply_context()
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = context
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        SurfaceWebhookReceivedEvent(source="telegram", payload={"update_id": 1}),
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
async def test_handle_surface_webhook_skips_queue_when_interaction_was_handled(
    monkeypatch,
):
    handler = AsyncMock()
    handler.try_handle_interaction.return_value = True
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        SurfaceWebhookReceivedEvent(source="telegram", payload={"callback_query": {}}),
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
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = None
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        SurfaceWebhookReceivedEvent(source="telegram", payload={"update_id": 2}),
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
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = None
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)
    surface_id = uuid4()

    await handlers.handle_surface_webhook(
        SurfaceWebhookReceivedEvent(
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
@pytest.mark.parametrize("has_context", [False, True])
async def test_schedule_surface_event_is_inbox_backed_and_deterministically_queued(
    monkeypatch, has_context
):
    handler = AsyncMock()
    context = _reply_context() if has_context else None
    handler.prepare_ingress.return_value = context
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)
    event = ScheduleFired(
        schedule_id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        payload={"message": "hello"},
        pod_id=uuid4(),
    )

    await handlers.handle_surface_schedule_event(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    ingress = handler.prepare_ingress.await_args.args[0]
    assert isinstance(ingress, handlers.SurfaceScheduleIngress)
    if has_context:
        job_queue.enqueue.assert_awaited_once()
        assert job_queue.enqueue.await_args.kwargs["_job_id"] == (
            f"surface-schedule-event:{event.event_id}"
        )
    else:
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
