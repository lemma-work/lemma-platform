import pytest
from unittest.mock import AsyncMock
from types import SimpleNamespace
from uuid import uuid4

from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
)
from app.modules.schedule.api.schemas.schedule_schemas import CreateScheduleRequest
from app.modules.schedule.domain.errors import (
    ScheduleInfrastructureError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.interfaces import ScheduleTarget
from app.modules.schedule.services.schedule_service import ScheduleService


@pytest.mark.asyncio
async def test_create_external_schedule_success():
    uow = AsyncMock()
    schedule_repo = AsyncMock()
    scheduler = AsyncMock()
    external_writer = AsyncMock()

    service = ScheduleService(
        uow=uow,
        schedule_repository=schedule_repo,
        scheduler_service=scheduler,
        external_schedule_writer=external_writer,
    )

    user_id = uuid4()
    account_id = uuid4()
    app_schedule_id = "gmail_new_email"

    schedule_create = ScheduleCreateEntity(
        user_id=user_id,
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=app_schedule_id,
        account_id=account_id,
        config={"some": "config"},
        visibility="PERSONAL",
    )

    created_schedule = ScheduleEntity(id=uuid4(), **schedule_create.model_dump())
    schedule_repo.create.return_value = created_schedule
    external_writer.create_provider_trigger.return_value = "provider_123"

    updated_schedule = created_schedule.model_copy(deep=True)
    updated_schedule.config["provider_trigger_id"] = "provider_123"
    schedule_repo.update.return_value = updated_schedule

    result = await service.create_schedule(schedule_create)

    assert result.schedule_type == ScheduleType.WEBHOOK
    assert result.config["provider_trigger_id"] == "provider_123"
    external_writer.create_provider_trigger.assert_called_once_with(created_schedule)
    schedule_repo.update.assert_called_once()


@pytest.mark.asyncio
async def test_delete_external_schedule():
    uow = AsyncMock()
    schedule_repo = AsyncMock()
    scheduler = AsyncMock()
    external_writer = AsyncMock()

    service = ScheduleService(
        uow=uow,
        schedule_repository=schedule_repo,
        scheduler_service=scheduler,
        external_schedule_writer=external_writer,
    )

    schedule_id = uuid4()
    existing_schedule = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id="gmail_new_email",
        account_id=uuid4(),
        config={"provider_trigger_id": "provider_123"},
    )
    schedule_repo.get.return_value = existing_schedule
    schedule_repo.delete.return_value = True

    await service.delete_schedule(schedule_id)

    external_writer.delete_provider_trigger.assert_called_once_with(existing_schedule)
    schedule_repo.delete.assert_called_once_with(schedule_id)


@pytest.mark.asyncio
async def test_create_schedule_no_provider_schedule_created():
    uow = AsyncMock()
    schedule_repo = AsyncMock()
    scheduler = AsyncMock()
    external_writer = AsyncMock()

    service = ScheduleService(
        uow=uow,
        schedule_repository=schedule_repo,
        scheduler_service=scheduler,
        external_schedule_writer=external_writer,
    )

    schedule_create = ScheduleCreateEntity(
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id="generic_webhook",
        account_id=uuid4(),
        config={"some": "config"},
        visibility="PERSONAL",
    )

    created_schedule = ScheduleEntity(id=uuid4(), **schedule_create.model_dump())
    schedule_repo.create.return_value = created_schedule
    external_writer.create_provider_trigger.return_value = None

    result = await service.create_schedule(schedule_create)

    assert result.id == created_schedule.id
    schedule_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_delete_external_schedule_failure_preserves_local_schedule():
    uow = AsyncMock()
    schedule_repo = AsyncMock()
    scheduler = AsyncMock()
    external_writer = AsyncMock()

    service = ScheduleService(
        uow=uow,
        schedule_repository=schedule_repo,
        scheduler_service=scheduler,
        external_schedule_writer=external_writer,
    )

    schedule_id = uuid4()
    existing_schedule = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id="gmail_new_email",
        account_id=uuid4(),
        config={"provider_trigger_id": "provider_123"},
    )
    schedule_repo.get.return_value = existing_schedule
    external_writer.delete_provider_trigger.side_effect = ScheduleInfrastructureError(
        "provider cleanup failed"
    )

    with pytest.raises(ScheduleInfrastructureError):
        await service.delete_schedule(schedule_id)

    schedule_repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_webhook_schedule_derives_trigger_from_workflow_start():
    service = ScheduleService(
        uow=AsyncMock(),
        schedule_repository=AsyncMock(),
        scheduler_service=AsyncMock(),
        external_schedule_writer=AsyncMock(),
    )
    workflow_id = uuid4()
    workflow = ScheduleTarget(
        id=workflow_id,
        pod_id=uuid4(),
        name="gmail_email_ingest",
        event_trigger_id="gmail:gmail_new_gmail_message",
        event_trigger_config={"labelIds": "INBOX"},
    )
    service._get_workflow_by_name = AsyncMock(  # type: ignore[method-assign]
        return_value=workflow
    )

    schedule_create = ScheduleCreateEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        workflow_name="gmail_email_ingest",
        account_id=uuid4(),
        config={"source": "composio"},
        filter_instruction="Only important mail",
        filter_output_schema={"type": "object"},
    )

    resolved = await service._resolve_create_target(schedule_create)

    assert resolved.workflow_id == workflow_id
    assert resolved.agent_id is None
    assert resolved.connector_trigger_id == "gmail:gmail_new_gmail_message"
    assert resolved.config == {"source": "composio", "labelIds": "INBOX"}
    assert resolved.filter_instruction == "Only important mail"
    assert resolved.filter_output_schema == {"type": "object"}


@pytest.mark.asyncio
async def test_workflow_webhook_schedule_rejects_trigger_mismatch():
    service = ScheduleService(
        uow=AsyncMock(),
        schedule_repository=AsyncMock(),
        scheduler_service=AsyncMock(),
        external_schedule_writer=AsyncMock(),
    )
    workflow = ScheduleTarget(
        id=uuid4(),
        pod_id=uuid4(),
        name="gmail_email_ingest",
        event_trigger_id="gmail:gmail_new_gmail_message",
        event_trigger_config={},
    )
    service._get_workflow_by_name = AsyncMock(  # type: ignore[method-assign]
        return_value=workflow
    )

    schedule_create = ScheduleCreateEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        workflow_name="gmail_email_ingest",
        connector_trigger_id="gmail:wrong_trigger",
        account_id=uuid4(),
        config={"source": "composio"},
    )

    with pytest.raises(ScheduleValidationError):
        await service._resolve_create_target(schedule_create)


def test_create_schedule_request_rejects_workflow_connector_trigger_id():
    with pytest.raises(ValueError):
        CreateScheduleRequest(
            schedule_type=ScheduleType.WEBHOOK,
            workflow_name="gmail_email_ingest",
            connector_trigger_id="gmail:gmail_new_gmail_message",
            config={"source": "composio"},
        )


@pytest.mark.asyncio
async def test_delete_all_for_pod_tears_down_every_schedule():
    schedule_repo = AsyncMock()
    service = ScheduleService(
        uow=AsyncMock(),
        schedule_repository=schedule_repo,
        scheduler_service=AsyncMock(),
        external_schedule_writer=AsyncMock(),
    )

    pod_id = uuid4()
    s1 = SimpleNamespace(id=uuid4())
    s2 = SimpleNamespace(id=uuid4())
    schedule_repo.list_all_by_pod.return_value = [s1, s2]
    service.delete_schedule = AsyncMock(return_value=True)  # type: ignore[method-assign]

    deleted = await service.delete_all_for_pod(pod_id)

    assert deleted == 2
    schedule_repo.list_all_by_pod.assert_awaited_once_with(pod_id)
    assert service.delete_schedule.await_count == 2


@pytest.mark.asyncio
async def test_delete_all_for_pod_force_deletes_on_teardown_failure():
    schedule_repo = AsyncMock()
    service = ScheduleService(
        uow=AsyncMock(),
        schedule_repository=schedule_repo,
        scheduler_service=AsyncMock(),
        external_schedule_writer=AsyncMock(),
    )

    pod_id = uuid4()
    failing = SimpleNamespace(id=uuid4())
    ok = SimpleNamespace(id=uuid4())
    schedule_repo.list_all_by_pod.return_value = [failing, ok]
    # First schedule's full teardown blows up; second succeeds normally.
    service.delete_schedule = AsyncMock(  # type: ignore[method-assign]
        side_effect=[ScheduleInfrastructureError("composio down"), True]
    )
    schedule_repo.delete.return_value = True

    deleted = await service.delete_all_for_pod(pod_id)

    # 1 from the fallback row delete + 1 from the normal delete.
    assert deleted == 2
    schedule_repo.delete.assert_awaited_once_with(failing.id)
