from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.pod.domain.events import PodCreatedEvent, PodJoinRequestedEvent
from app.modules.pod.events import pod_handlers
from app.modules.datastore.events import pod_schema_consumer
from app.modules.test_support.fakes import PassthroughEventInbox


def _created_event() -> PodCreatedEvent:
    return PodCreatedEvent(
        pod_id=uuid4(),
        organization_id=uuid4(),
        creator_id=uuid4(),
        name="reliable-pod",
    )


@pytest.mark.asyncio
async def test_pod_created_wrapper_ignores_other_events() -> None:
    inbox = SimpleNamespace(process=AsyncMock())

    await pod_schema_consumer.on_pod_created(
        {"event_type": "pod.updated"}, Mock(), inbox=inbox
    )

    inbox.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_pod_created_wrapper_claims_inbox_before_processing(monkeypatch) -> None:
    event = _created_event().model_dump(mode="json")
    manager = SimpleNamespace(create_datastore_schema=AsyncMock())
    monkeypatch.setattr(pod_schema_consumer, "SchemaManager", lambda: manager)
    logger = Mock()

    await pod_schema_consumer.on_pod_created(
        event, logger, inbox=PassthroughEventInbox()
    )

    manager.create_datastore_schema.assert_awaited_once()
    assert str(manager.create_datastore_schema.await_args.args[0]) == event["pod_id"]


@pytest.mark.asyncio
async def test_pod_schema_creation_is_idempotently_delegated(monkeypatch) -> None:
    event = _created_event()
    manager = SimpleNamespace(create_datastore_schema=AsyncMock())
    monkeypatch.setattr(pod_schema_consumer, "SchemaManager", lambda: manager)

    await pod_schema_consumer.on_pod_created(
        event.model_dump(mode="json"),
        Mock(),
        inbox=PassthroughEventInbox(),
    )

    manager.create_datastore_schema.assert_awaited_once_with(event.pod_id)


@pytest.mark.asyncio
async def test_pod_schema_failure_rethrows_for_inbox_retry(monkeypatch) -> None:
    class DependencyFailure(RuntimeError):
        pass

    event = _created_event()
    manager = SimpleNamespace(
        create_datastore_schema=AsyncMock(side_effect=DependencyFailure())
    )
    monkeypatch.setattr(pod_schema_consumer, "SchemaManager", lambda: manager)

    with pytest.raises(DependencyFailure):
        await pod_schema_consumer.on_pod_created(
            event.model_dump(mode="json"),
            Mock(),
            inbox=PassthroughEventInbox(),
        )


@pytest.mark.asyncio
async def test_join_request_wrapper_projects_inside_inbox(monkeypatch) -> None:
    event = PodJoinRequestedEvent(
        pod_id=uuid4(),
        organization_id=uuid4(),
        requester_user_id=uuid4(),
        join_request_id=uuid4(),
    ).model_dump(mode="json")
    process = AsyncMock()
    monkeypatch.setattr(pod_handlers, "_process_pod_join_requested", process)
    logger = Mock()
    uow_factory = Mock()
    email_port = Mock()

    await pod_handlers.on_pod_join_requested(
        event,
        logger,
        uow_factory=uow_factory,
        email_port=email_port,
        inbox=PassthroughEventInbox(),
    )

    parsed = process.await_args.args[0]
    assert str(parsed.join_request_id) == event["join_request_id"]
    assert process.await_args.kwargs == {
        "uow_factory": uow_factory,
        "email_port": email_port,
    }
