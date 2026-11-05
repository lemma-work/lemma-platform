"""A turn under spend limits is decided on the windows' running totals.

A system-model turn under a limit is asked whether it may run before its first
model request goes out, twice: when its message is appended
(``usage_preflight``) and when a worker starts the run (``check_run_budget``).
Each request is asked again before it is dispatched. All of them compare the
same windows (``budget_windows``) on their running totals (``spend_counters``),
so once a window has been counted none of them sums the ledger -- a sum that
would otherwise scan the member's whole month on every turn, and grow with it.

Real turns against real Postgres: what is asserted is which statements run,
and a fake cannot issue the wrong ones.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import func, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.usage_preflight import assert_usage_preflight_allowed
from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    drain,
    turn_pod,
    turn_registry,
)
from app.modules.test_support.e2e.scripted_model import script_text
from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
    statements_touching,
)
from app.modules.usage.config import usage_settings
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.execution import build_usage_service
from app.modules.usage.contracts.metering import check_run_budget
from app.modules.usage.domain.accounting import CONTACT_RUN
from app.modules.usage.infrastructure.cost_expressions import recorded_cost
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord

pytestmark = [pytest.mark.e2e]


def _sums(statements: list[str]) -> list[str]:
    """The statements that add spend up, from the ledger or the counters."""
    return [
        statement
        for statement in statements
        if "sum(" in statement.lower()
        and ("usage_records" in statement or "usage_limit_counters" in statement)
    ]


async def _on_the_system_model(pod: TurnPod) -> None:
    """Point the pod's agent at the platform's own model, which limits meter."""
    name = f"{pod.agent_name}_system"
    response = await pod.client.post(
        f"/pods/{pod.pod_id}/agents",
        json={
            "name": name,
            "instruction": "Follow the script.",
            "agent_runtime": {
                "profile_id": "system:lemma",
                "model_name": "e2e-mock-model",
            },
            "toolsets": [],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod.agent_name = name


async def _weekly(user_id: UUID) -> UsageLimitCounter:
    async with async_session_maker() as session:
        return (
            await session.scalars(
                select(UsageLimitCounter).where(
                    UsageLimitCounter.user_id == user_id,
                    UsageLimitCounter.window_kind == "user_week",
                )
            )
        ).one()


async def _spent_this_week(user_id: UUID) -> Decimal:
    now = datetime.now(timezone.utc)
    week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.coalesce(func.sum(recorded_cost()), 0)).where(
                UsageRecord.user_id == user_id,
                UsageRecord.profile_scope == "SYSTEM",
                UsageRecord.occurred_at >= week,
            )
        ) or Decimal(0)


@pytest.mark.drives_work
async def test_a_counted_turn_under_limits_never_sums_the_ledger(
    authenticated_client, fixed_test_org, fixed_test_user, monkeypatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_org_monthly_limit_usd", 1000.0)
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1000.0)
    monkeypatch.setattr(usage_settings, "usage_user_monthly_limit_usd", 1000.0)
    user_id = UUID(fixed_test_user["id"])
    pod = await turn_pod(authenticated_client, fixed_test_org["id"])
    await _on_the_system_model(pod)
    # The first turn in a window counts it from the ledger: its counters are new.
    await pod.append(await pod.conversation([script_text("Counted.")]))
    await drain(turn_registry())

    conversation = await pod.conversation([script_text("Steady.")])
    with counted_queries() as appended:
        started = await pod.append(conversation)
    with counted_queries() as ran:
        await drain(turn_registry())

    assert await pod.run_status(started["agent_run_id"]) == "COMPLETED"
    # Asked, and answered from the counters: the preflight read them...
    assert statements_touching(appended, "usage_limit_counters"), format_statements(
        appended
    )
    assert not _sums(appended), format_statements(_sums(appended))
    # ...and so did the run's start and each of its requests.
    assert not _sums(ran), format_statements(_sums(ran))
    # Still the ledger's total, every receipt of both turns included.
    counter = await _weekly(user_id)
    assert counter.used_usd > 0
    assert counter.used_usd == await _spent_this_week(user_id)


@pytest.mark.drives_work
async def test_a_member_past_their_limit_is_refused_by_name_without_a_ledger_sum(
    authenticated_client, fixed_test_org, fixed_test_user, monkeypatch
) -> None:
    # The first request's own cost takes the week past this.
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 0.000000001)
    user_id = UUID(fixed_test_user["id"])
    pod = await turn_pod(authenticated_client, fixed_test_org["id"])
    await _on_the_system_model(pod)
    await pod.append(await pod.conversation([script_text("Over.")]))
    await drain(turn_registry())
    assert (await _weekly(user_id)).used_usd > Decimal("0.000000001")

    conversation = await pod.conversation([script_text("Never sent.")])
    with counted_queries() as refused:
        response = await pod.client.post(
            f"/pods/{pod.pod_id}/conversations/{conversation}/messages/append",
            json={"content": "Once more."},
        )

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS, response.text
    body = response.json()
    assert body["code"] == "USAGE_LIMIT_EXCEEDED", body
    assert body["message"] == "LLM usage limit exceeded: user weekly limit reached"
    assert not _sums(refused), format_statements(_sums(refused))

    # A run that starts some other way -- a schedule, a retry -- meets the same
    # window at its start, and is refused on the same total.
    with counted_queries() as checked, pytest.raises(UsageLimitExceededError):
        await check_run_budget(
            factory=SessionUnitOfWorkFactory(async_session_maker),
            organization_id=UUID(fixed_test_org["id"]),
            user_id=user_id,
            profile_scope="SYSTEM",
        )
    assert not _sums(checked), format_statements(_sums(checked))


async def _spent(
    organization_id: UUID, user_id: UUID, *, source_type: str, cost: str
) -> None:
    """A priced row written outside settlement, which no counter has counted."""
    async with async_session_maker() as session, session.begin():
        session.add(
            UsageRecord(
                organization_id=organization_id,
                user_id=user_id,
                source_type=source_type,
                profile_id="system:lemma",
                profile_scope="SYSTEM",
                model_name="e2e-mock-model",
                occurred_at=datetime.now(timezone.utc),
                cost_amount=Decimal(cost),
            )
        )


async def test_the_door_holds_a_run_for_people_outside_to_the_organization(
    monkeypatch,
) -> None:
    """A contact's run is the organization's spend: the member who looks after
    the conversation having spent their own week does not stop it, and the
    organization's cap on answering contacts does."""
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    monkeypatch.setattr(usage_settings, "usage_contacts_monthly_default_usd", 2.0)
    organization_id, user_id = uuid4(), uuid4()
    await _spent(organization_id, user_id, source_type="agent_run", cost="1.5")

    async def door(*, outside_audience: bool) -> None:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            await assert_usage_preflight_allowed(
                build_usage_service(uow),
                organization_id=organization_id,
                user_id=user_id,
                agent_runtime=AgentRuntimeConfig(
                    profile_id="system:lemma", model_name="e2e-mock-model"
                ),
                outside_audience=outside_audience,
            )

    with pytest.raises(UsageLimitExceededError) as members:
        await door(outside_audience=False)
    assert members.value.message == (
        "LLM usage limit exceeded: user weekly limit reached"
    )
    await door(outside_audience=True)

    await _spent(organization_id, user_id, source_type=CONTACT_RUN, cost="2")
    with pytest.raises(UsageLimitExceededError) as contacts:
        await door(outside_audience=True)
    assert contacts.value.message == (
        "LLM usage limit exceeded: contacts monthly limit reached"
    )
