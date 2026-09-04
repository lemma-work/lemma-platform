"""A worker that never came back still leaves a bill behind.

`PS-OPS-003` says a run's usage is recorded "however the run ended". The way a
run ends that nothing could record was the worker dying: spend lived in the
worker's memory until the run finished, so a SIGKILL took the tokens with it and
the run was billed zero for what the provider had already sold.

Spend is now written to the run's own row as each model request lands, and
`reconcile_orphaned_agent_runs` bills it. Driven against a real database rather
than with doubles, because the whole mechanism is two row-locked claims and a
JSONB merge -- the parts a stand-in would certify without exercising.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.events.orphan_reservations import settle_orphaned_run
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.usage.infrastructure.models import UsageRecord as UsageRecordModel
from app.modules.usage.services.usage_service import ModelPricing, UsageService

pytestmark = pytest.mark.e2e


async def _an_abandoned_run(session, scenario) -> uuid.UUID:
    """A real organization and pod, and a run inside them that nobody owns."""
    await scenario.create_org_with_pod(name_prefix="Abandoned Run")
    return await _a_run_in_flight(
        session,
        organization_id=uuid.UUID(scenario.org_id),
        pod_id=uuid.UUID(scenario.pod_id),
        user_id=uuid.UUID(scenario.owner_user["id"]),
    )


@pytest.fixture(autouse=True)
def _priced_model():
    UsageService.register_model_pricing({"abandoned-model": ModelPricing(1.0, 2.0)})
    yield
    UsageService._SYSTEM_MODEL_PRICING.pop("abandoned-model", None)


async def _a_run_in_flight(session, *, organization_id, pod_id, user_id) -> uuid.UUID:
    """A conversation and a RUNNING agent run, written straight to the tables.

    Going through the API would start a real run, which is the one thing this
    cannot have: the point is a run whose worker is *gone*, and a live worker
    would finalize it before the reconciler ever saw it.
    """
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO agent_conversations "
            "(id, user_id, pod_id, organization_id, conversation_type, "
            " created_at, updated_at) "
            "VALUES (:i, :u, :p, :o, 'CHAT', now(), now())"
        ),
        {"i": conversation_id, "u": user_id, "p": pod_id, "o": organization_id},
    )
    await session.execute(
        text(
            "INSERT INTO agent_runs "
            "(id, conversation_id, status, agent_runtime, started_at, "
            " created_at, updated_at) "
            "VALUES (:i, :c, 'RUNNING', :r, :s, now(), now())"
        ),
        {
            "i": run_id,
            "c": conversation_id,
            "r": '{"profile_id": "system:lemma", "scope": "SYSTEM", '
            '"model_name": "abandoned-model"}',
            "s": datetime.now(timezone.utc) - timedelta(hours=1),
        },
    )
    await session.commit()
    return run_id


async def test_spend_survives_the_worker_that_was_spending_it(db_session, scenario):
    run_id = await _an_abandoned_run(db_session, scenario)
    uow = SqlAlchemyUnitOfWork(db_session, message_bus=None)
    repository = ConversationRepository(uow)

    # Two model requests land, and then the worker is gone. Absolute writes, so
    # the second is the attempt's running total rather than an increment.
    await repository.store_attempt_usage(
        agent_run_id=run_id,
        attempt_id="attempt-1",
        usage={
            "model_name": "abandoned-model",
            "input_tokens": 1_000,
            "output_tokens": 100,
            "request_count": 1,
        },
    )
    await repository.store_attempt_usage(
        agent_run_id=run_id,
        attempt_id="attempt-1",
        usage={
            "model_name": "abandoned-model",
            "input_tokens": 3_000,
            "output_tokens": 400,
            "request_count": 2,
        },
    )
    await db_session.commit()

    await settle_orphaned_run(uow, repository, run_id)
    await db_session.commit()

    record = (
        await db_session.execute(
            select(UsageRecordModel).where(UsageRecordModel.agent_run_id == run_id)
        )
    ).scalar_one()

    assert record.input_tokens == 3_000
    assert record.output_tokens == 400
    # Priced, not merely counted: 3000/1e6 * $1.00 + 400/1e6 * $2.00.
    assert record.cost_usd == pytest.approx(0.0038)
    assert (record.record_metadata or {}).get("reconciled") is True


async def test_a_reclaimed_run_is_billed_for_both_of_its_attempts(db_session, scenario):
    """A run keeps its id across a restart; its spend has to keep adding up.

    This is why spend is keyed by attempt rather than kept as one total. A flat
    figure would have to be read before it could be added to, and the worker
    that reclaims a run has no idea what the previous one had reached.
    """
    run_id = await _an_abandoned_run(db_session, scenario)
    uow = SqlAlchemyUnitOfWork(db_session, message_bus=None)
    repository = ConversationRepository(uow)

    for attempt, tokens in (("attempt-1", 1_000), ("attempt-2", 500)):
        await repository.store_attempt_usage(
            agent_run_id=run_id,
            attempt_id=attempt,
            usage={
                "model_name": "abandoned-model",
                "input_tokens": tokens,
                "output_tokens": 0,
                "request_count": 1,
            },
        )
    await db_session.commit()

    await settle_orphaned_run(uow, repository, run_id)
    await db_session.commit()

    record = (
        await db_session.execute(
            select(UsageRecordModel).where(UsageRecordModel.agent_run_id == run_id)
        )
    ).scalar_one()

    assert record.input_tokens == 1_500


async def test_the_same_spend_is_never_billed_twice(db_session, scenario):
    """The worker may be alive after all and finalize a moment later.

    Both it and the reconciler reach for the same accumulated spend, and exactly
    one of them may have it -- billing the same tokens twice is worse than the
    gap that would leave.
    """
    run_id = await _an_abandoned_run(db_session, scenario)
    uow = SqlAlchemyUnitOfWork(db_session, message_bus=None)
    repository = ConversationRepository(uow)
    await repository.store_attempt_usage(
        agent_run_id=run_id,
        attempt_id="attempt-1",
        usage={
            "model_name": "abandoned-model",
            "input_tokens": 2_000,
            "output_tokens": 0,
            "request_count": 1,
        },
    )
    await db_session.commit()

    await settle_orphaned_run(uow, repository, run_id)
    await db_session.commit()
    await settle_orphaned_run(uow, repository, run_id)
    await db_session.commit()

    records = (
        (
            await db_session.execute(
                select(UsageRecordModel).where(UsageRecordModel.agent_run_id == run_id)
            )
        )
        .scalars()
        .all()
    )

    assert len(records) == 1
    # And the run's row is left with nothing further to settle.
    accumulated = (
        await db_session.execute(
            select(AgentRunModel.usage_accumulated).where(AgentRunModel.id == run_id)
        )
    ).scalar_one()
    assert accumulated is None
