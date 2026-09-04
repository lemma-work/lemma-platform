"""The usage API tells you what a cost is made of, and who may ask.

Two regressions with the same root: numbers that were computed correctly and then
not shown. The cached-input discount was applied to every price and reported
nowhere, so a heavily cached run and an uncached one of the same size differed
tenfold in cost with nothing on screen to explain it. And a person could not read
their own usage at all without administrative access, which `PS-OPS-002` says
they must not need.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.test_support.e2e_authz import auth_headers
from app.modules.usage.domain.entities import CostSource, UsageRecord
from app.modules.usage.infrastructure.repositories import UsageRepository

pytestmark = pytest.mark.e2e


def _record(
    *,
    organization_id: UUID,
    user_id: UUID,
    model_name: str = "priced-model",
    profile_scope: str = "SYSTEM",
    input_tokens: int = 1000,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 200,
    cost_usd: float | None = 0.5,
    cost_source: CostSource = CostSource.REGISTERED,
) -> UsageRecord:
    return UsageRecord(
        organization_id=organization_id,
        user_id=user_id,
        source_type="AGENT_RUN",
        profile_id="system:lemma",
        profile_scope=profile_scope,
        model_name=model_name,
        usage_kind="LLM",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
        cost_source=cost_source,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )


async def _seed(db_session, records: list[UsageRecord]) -> None:
    uow = SqlAlchemyUnitOfWork(db_session, message_bus=None)
    repository = UsageRepository(uow)
    for record in records:
        await repository.create(record)
    await db_session.commit()


async def test_a_usage_record_reports_its_cached_and_uncached_split(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    organization_id = UUID(fixed_test_org["id"])
    user_id = UUID(fixed_test_user["id"])
    await _seed(
        db_session,
        [
            _record(
                organization_id=organization_id,
                user_id=user_id,
                input_tokens=1000,
                cached_input_tokens=700,
                cache_write_tokens=100,
            )
        ],
    )

    response = await authenticated_client.get(
        f"/usage/organizations/{organization_id}/events", params={"days": 1}
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    event = response.json()["items"][0]

    assert event["input_tokens"] == 1000
    assert event["cached_input_tokens"] == 700
    assert event["cache_write_tokens"] == 100
    # The remainder, and the only part billed at the full rate.
    assert event["uncached_input_tokens"] == 200
    assert event["cost_source"] == "REGISTERED"


async def test_the_summary_aggregates_the_split_and_separates_system_spend(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """`system_cost_usd` is the plan-limit number; `total_cost_usd` includes BYO.

    A runtime profile someone added bills their own provider. Folding it into the
    figure an allowance is measured against would show an organization a bill it
    does not owe -- and hiding it entirely is what used to leave those rows
    costed at null forever.
    """
    organization_id = UUID(fixed_test_org["id"])
    user_id = UUID(fixed_test_user["id"])
    await _seed(
        db_session,
        [
            _record(
                organization_id=organization_id,
                user_id=user_id,
                input_tokens=1000,
                cached_input_tokens=600,
                cost_usd=0.50,
            ),
            _record(
                organization_id=organization_id,
                user_id=user_id,
                model_name="byo-model",
                profile_scope="ORGANIZATION",
                input_tokens=500,
                cached_input_tokens=100,
                cost_usd=4.00,
                cost_source=CostSource.ESTIMATED,
            ),
        ],
    )

    response = await authenticated_client.get(
        f"/usage/organizations/{organization_id}/summary", params={"days": 1}
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    summary = response.json()

    assert summary["total_input_tokens"] == 1500
    assert summary["total_cached_input_tokens"] == 700
    assert summary["total_uncached_input_tokens"] == 800
    assert summary["system_cost_usd"] == pytest.approx(0.50)
    assert summary["total_cost_usd"] == pytest.approx(4.50)
    assert summary["total_by_model"]["byo-model"]["system_cost_usd"] == pytest.approx(
        0.0
    )


async def test_the_time_series_carries_the_split_too(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    organization_id = UUID(fixed_test_org["id"])
    user_id = UUID(fixed_test_user["id"])
    await _seed(
        db_session,
        [
            _record(
                organization_id=organization_id,
                user_id=user_id,
                input_tokens=800,
                cached_input_tokens=500,
            )
        ],
    )

    response = await authenticated_client.get(
        f"/usage/organizations/{organization_id}/stats",
        params={"days": 1, "granularity": "day"},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    bucket = response.json()["items"][0]

    assert bucket["cached_input_tokens"] == 500
    assert bucket["uncached_input_tokens"] == 300


async def test_a_member_can_read_their_own_usage_without_being_an_admin(
    async_client,
    scenario,
    db_session,
):
    """`PS-OPS-002`, which the owner/editor gate on this endpoint contradicted.

    The people most likely to run into a spend limit were exactly the ones who
    could not look up how much of it they had used. A pod viewer is about as far
    from an administrator as a member gets.
    """
    await scenario.create_org_with_pod(name_prefix="Own Usage")
    member = await scenario.create_user("usage-member")
    await scenario.add_user_to_pod(user=member, role="POD_VIEWER")
    organization_id = UUID(scenario.org_id)
    await _seed(
        db_session,
        [
            _record(
                organization_id=organization_id,
                user_id=UUID(member["id"]),
                cost_usd=0.25,
            )
        ],
    )

    response = await async_client.get(
        f"/usage/organizations/{organization_id}/me",
        params={"days": 1},
        headers=auth_headers(member),
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["system_cost_usd"] == pytest.approx(0.25)


async def test_a_non_member_is_still_refused_their_own_usage(
    async_client,
    fixed_test_org,
    scenario,
):
    """Widening the gate to membership must not widen it past membership."""
    outsider = await scenario.create_user("usage-outsider")

    response = await async_client.get(
        f"/usage/organizations/{fixed_test_org['id']}/me",
        params={"days": 1},
        headers=auth_headers(outsider),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text


async def test_an_unpriced_model_is_recorded_with_an_unknown_cost_not_zero(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """`PS-OPS-001`: unknown is not the same claim as free."""
    organization_id = UUID(fixed_test_org["id"])
    await _seed(
        db_session,
        [
            _record(
                organization_id=organization_id,
                user_id=UUID(fixed_test_user["id"]),
                model_name=f"unpriceable-{uuid4().hex[:8]}",
                cost_usd=None,
                cost_source=CostSource.UNKNOWN,
            )
        ],
    )

    response = await authenticated_client.get(
        f"/usage/organizations/{organization_id}/events", params={"days": 1}
    )
    event = response.json()["items"][0]

    assert event["cost_usd"] is None
    assert event["cost_source"] == "UNKNOWN"
