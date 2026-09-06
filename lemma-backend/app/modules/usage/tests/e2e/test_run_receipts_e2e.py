"""Run outcomes label every request without changing monetary settlement."""

from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.services.run_usage_recorder import RunUsageRecorder
from app.modules.agent.services.run_finalizer import RunFinalizer
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.models.organization_models import Organization
from app.modules.pod.infrastructure.models.pod_models import Pod
from app.modules.usage.domain.accounting import RequestReceipt, TokenCounts
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.tests.fakes import FailOnceUnitOfWorkFactory

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "STOPPED"])
async def test_finalization_labels_flushed_and_pending_receipts_without_rebilling(
    db_manager: DatabaseManager, status: str
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    run_id, other_run_id, user_id = uuid4(), uuid4(), uuid4()
    profile: dict[str, object | None] = {
        "profile_id": "test:receipt-status",
        "scope": "ORGANIZATION",
        "model_name": "test",
        "protocol": "OPENAI_COMPATIBLE",
    }
    async with factory() as uow:
        uow.session.add(
            UsageRecord(
                user_id=user_id,
                agent_run_id=other_run_id,
                source_type="agent_run",
                profile_id="other",
                profile_scope="ORGANIZATION",
                model_name="test",
                cost_amount=Decimal("1"),
            )
        )
    async with metering_execution(
        UsageExecutionContext(
            user_id=user_id, organization_id=None, pod_id=None, agent_run_id=run_id
        ),
        factory=factory,
    ) as scope:
        meter, _ = scope.meter(profile, None)
        for _ in range(3):
            request_id, occurred_at, _ = await meter.before(priceable=True)
            await meter.after(
                RequestReceipt(
                    request_id=request_id,
                    occurred_at=occurred_at,
                    counts=TokenCounts(input_tokens=1, request_count=1),
                    cost=Decimal("0.000000001"),
                )
            )
        recorder = RunUsageRecorder(factory)
        for _ in range(2):
            await recorder.finalize_metered(
                agent_run_id=run_id, runtime_profile=profile, status=status
            )
        async with factory() as uow:
            records = list(
                await uow.session.scalars(
                    select(UsageRecord).where(UsageRecord.agent_run_id == run_id)
                )
            )
            assert len(records) == 3
            assert {record.status for record in records} == {status}
            assert sum(
                (record.cost_amount or Decimal(0) for record in records), Decimal(0)
            ) == Decimal("0.000000003")
            assert sum(record.input_tokens for record in records) == 3
            other = (
                await uow.session.scalars(
                    select(UsageRecord).where(UsageRecord.agent_run_id == other_run_id)
                )
            ).one()
            assert other.status is None
            assert other.cost_amount == Decimal("1")


async def test_finalizer_retry_labels_receipts_with_committed_status_after_failure(
    db_manager: DatabaseManager,
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id, org_id, pod_id, conversation_id, run_id = (uuid4() for _ in range(5))
    profile: dict[str, object | None] = {
        "protocol": "OPENAI_COMPATIBLE",
        "scope": "ORGANIZATION",
        "profile_id": "test:receipt-retry",
    }
    async with factory() as uow:
        uow.session.add_all(
            [
                User(id=user_id, email=f"{user_id}@example.test"),
                Organization(id=org_id, name="Receipt retry", slug=f"receipt-{org_id}"),
            ]
        )
        await uow.session.flush()
        uow.session.add(
            Pod(id=pod_id, user_id=user_id, organization_id=org_id, name="Retry")
        )
        await uow.session.flush()
        uow.session.add(
            ConversationModel(
                id=conversation_id,
                user_id=user_id,
                pod_id=pod_id,
                organization_id=org_id,
                status="RUNNING",
            )
        )
        await uow.session.flush()
        uow.session.add_all(
            [
                AgentRunModel(
                    id=run_id,
                    conversation_id=conversation_id,
                    started_at=datetime.now(timezone.utc),
                    status="RUNNING",
                ),
                UsageRecord(
                    user_id=user_id,
                    agent_run_id=run_id,
                    request_id=uuid4(),
                    source_type="agent_run",
                    profile_id="test:receipt-retry",
                    profile_scope="ORGANIZATION",
                    model_name="test",
                    cost_amount=Decimal(".1"),
                ),
            ]
        )
    recorder = RunUsageRecorder(FailOnceUnitOfWorkFactory(factory))
    finalizer = RunFinalizer(factory, recorder)
    identity = RunIdentity(
        conversation_id=conversation_id,
        agent_run_id=run_id,
        runtime_profile=profile,
        user_id=user_id,
        organization_id=org_id,
        pod_id=pod_id,
    )
    with pytest.raises(ConnectionError, match="receipt status transaction unavailable"):
        await finalizer.finish(run=identity, status=AgentRunStatus.COMPLETED)
    async with factory() as uow:
        persisted = await uow.session.get(AgentRunModel, run_id)
        assert persisted is not None and persisted.status == "COMPLETED"
        receipt = (
            await uow.session.scalars(
                select(UsageRecord).where(UsageRecord.agent_run_id == run_id)
            )
        ).one()
        assert receipt.status is None
    await finalizer.finish(run=identity, status=AgentRunStatus.FAILED)
    async with factory() as uow:
        persisted = await uow.session.get(AgentRunModel, run_id)
        assert persisted is not None and persisted.status == "COMPLETED"
        receipt = (
            await uow.session.scalars(
                select(UsageRecord).where(UsageRecord.agent_run_id == run_id)
            )
        ).one()
        assert receipt.status == "COMPLETED"
        assert receipt.cost_amount == Decimal(".1")


async def test_failed_run_persists_structured_reason_without_overwriting_terminal_state(
    db_manager: DatabaseManager,
) -> None:
    from app.modules.agent.infrastructure.repositories.conversation_repository import (
        ConversationRepository,
    )

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id, org_id, pod_id, conversation_id, run_id = (uuid4() for _ in range(5))
    async with factory() as uow:
        uow.session.add_all(
            [
                User(id=user_id, email=f"{user_id}@example.test"),
                Organization(id=org_id, name="Usage review", slug=f"usage-{org_id}"),
            ]
        )
        await uow.session.flush()
        uow.session.add(
            Pod(id=pod_id, user_id=user_id, organization_id=org_id, name="Usage")
        )
        await uow.session.flush()
        uow.session.add(
            ConversationModel(
                id=conversation_id,
                user_id=user_id,
                pod_id=pod_id,
                organization_id=org_id,
                status="RUNNING",
            )
        )
        await uow.session.flush()
        uow.session.add(
            AgentRunModel(
                id=run_id,
                conversation_id=conversation_id,
                started_at=datetime.now(timezone.utc),
                status="RUNNING",
                run_metadata={"preserved": True},
            )
        )
    async with factory() as uow:
        repository = ConversationRepository(uow)
        await repository.finish_agent_run(
            agent_run_id=run_id,
            status=AgentRunStatus.FAILED,
            error="Allowance exhausted",
            error_code="USAGE_LIMIT_EXCEEDED",
            error_reason="exhausted",
        )
        await repository.finish_agent_run(
            agent_run_id=run_id, status=AgentRunStatus.COMPLETED
        )
    async with factory() as uow:
        run = await uow.session.get(AgentRunModel, run_id)
        assert run is not None
        assert run.status == "FAILED"
        assert run.run_metadata == {
            "preserved": True,
            "failure": {"code": "USAGE_LIMIT_EXCEEDED", "reason": "exhausted"},
        }
        conversation = await uow.session.get(ConversationModel, conversation_id)
        assert conversation is not None
        await uow.session.refresh(conversation, ["agent_runs"])
        restored = conversation.to_entity()
        assert restored.last_run_error_code == "USAGE_LIMIT_EXCEEDED"
        assert restored.last_run_error_reason == "exhausted"
