"""Run outcomes label every batch without changing monetary settlement."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.services.run_usage_recorder import RunUsageRecorder
from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.accounting import TokenCounts
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.services.metering_scope import metering_execution
from app.modules.usage.services.usage_context import UsageExecutionContext

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
        settings=UsageSettings(usage_batch_requests=2),
    ) as scope:
        meter, _ = scope.meter(profile, None)
        for _ in range(3):
            ticket = await meter.before(None)
            await meter.after(
                ticket,
                TokenCounts(input_tokens=1, request_count=1),
                Decimal("0.000000001"),
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
            assert len(records) == 2
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
