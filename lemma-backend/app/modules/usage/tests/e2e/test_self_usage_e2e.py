"""Self-service reports keep funding boundaries and tenant authorization intact."""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import AsyncClient
import pytest
from sqlalchemy import update

from app.core.api.dependencies import UoWDep
from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.usage.api.dependencies import get_usage_service
from app.modules.usage.domain.ports import UsageLimitValues
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.infrastructure.repositories import UsageRepository
from app.modules.usage.services.usage_service import UsageService

pytestmark = pytest.mark.e2e


class PlanLimits:
    def __init__(self) -> None:
        self.values = UsageLimitValues()

    async def resolve_limits(
        self, *, organization_id: UUID | None, user_id: UUID
    ) -> UsageLimitValues:
        return self.values


@pytest.fixture
def plan(test_app: FastAPI) -> Iterator[PlanLimits]:
    port = PlanLimits()

    def service(uow: UoWDep) -> UsageService:
        return UsageService(
            usage_repository=UsageRepository(uow), usage_limit_port=port
        )

    test_app.dependency_overrides[get_usage_service] = service
    try:
        yield port
    finally:
        test_app.dependency_overrides.pop(get_usage_service, None)


def receipt(org: UUID | None, user: UUID, amount: str, when: datetime) -> UsageRecord:
    return UsageRecord(
        organization_id=org,
        user_id=user,
        source_type="agent_run",
        profile_id="system:test",
        profile_scope="SYSTEM",
        model_name="test",
        usage_kind="llm",
        input_tokens=10,
        output_tokens=2,
        units=0,
        cost_amount=Decimal(amount),
        occurred_at=when,
    )


async def test_personal_reports_exclude_paid_organizations_and_other_users(
    authenticated_client: AsyncClient,
    fixed_test_org: dict[str, object],
    fixed_test_user: dict[str, str],
    db_manager: DatabaseManager,
    plan: PlanLimits,
) -> None:
    user = UUID(fixed_test_user["id"])
    organization = UUID(str(fixed_test_org["id"]))
    paid_org = uuid4()
    plan.values = UsageLimitValues(
        user_weekly_limit_usd=10,
        user_limit_scope="global",
        excluded_organization_ids=(paid_org,),
        plan_type="PERSONAL",
        plan_name="Personal",
    )
    now = datetime.now(timezone.utc)
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        uow.session.add_all(
            [
                receipt(organization, user, "1", now),
                receipt(None, user, "2", now),
                receipt(paid_org, user, "20", now),
                receipt(organization, uuid4(), "40", now),
            ]
        )
    params = {
        "organization_id": str(organization),
        "start": (now - timedelta(minutes=1)).isoformat(),
    }
    summary = await authenticated_client.get("/usage/me/summary", params=params)
    assert summary.status_code == 200, summary.text
    assert summary.json()["system_cost_usd"] == 3
    events = await authenticated_client.get("/usage/me/events", params=params)
    assert events.status_code == 200, events.text
    assert len(events.json()["items"]) == 2
    assert sorted(item["cost_usd"] for item in events.json()["items"]) == [1, 2]
    stats = await authenticated_client.get("/usage/me/stats", params=params)
    assert stats.status_code == 200, stats.text
    assert sum(bucket["system_cost_usd"] for bucket in stats.json()["items"]) == 3
    denied = await authenticated_client.get(
        "/usage/me/summary", params={"organization_id": str(uuid4())}
    )
    assert denied.status_code == 403


async def test_member_sees_shared_percentage_but_not_shared_dollars(
    authenticated_client: AsyncClient,
    fixed_test_org: dict[str, object],
    fixed_test_user: dict[str, str],
    db_manager: DatabaseManager,
    plan: PlanLimits,
) -> None:
    user = UUID(fixed_test_user["id"])
    organization = UUID(str(fixed_test_org["id"]))
    plan.values = UsageLimitValues(
        org_monthly_limit_usd=10,
        user_weekly_limit_usd=5,
        plan_type="TEAM",
        plan_name="Team",
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        await uow.session.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization,
                OrganizationMember.user_id == user,
            )
            .values(role="ORG_MEMBER")
        )
        uow.session.add_all(
            [
                receipt(organization, user, "1", datetime.now(timezone.utc)),
                receipt(organization, uuid4(), "5", datetime.now(timezone.utc)),
            ]
        )
    response = await authenticated_client.get(
        "/usage/me/limits", params={"organization_id": str(organization)}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["plan_type"] == "TEAM"
    assert {window["key"]: window["used_percent"] for window in data["windows"]} == {
        "user_weekly": 20,
        "org_monthly": 60,
    }
    assert "usd" not in response.text
    denied = await authenticated_client.get(
        f"/usage/organizations/{organization}/summary"
    )
    assert denied.status_code == 403
    own = await authenticated_client.get(
        "/usage/me/summary", params={"organization_id": str(organization)}
    )
    assert own.status_code == 200, own.text
    assert own.json()["system_cost_usd"] == 1


@pytest.mark.parametrize("cap,expected", [(None, []), (0, [100]), (1, [200])])
async def test_unlimited_zero_and_overshoot(
    authenticated_client: AsyncClient,
    fixed_test_user: dict[str, str],
    db_manager: DatabaseManager,
    plan: PlanLimits,
    cap: float | None,
    expected: list[int],
) -> None:
    user = UUID(fixed_test_user["id"])
    plan.values = UsageLimitValues(
        user_monthly_limit_usd=cap, user_limit_scope="global", plan_type="PERSONAL"
    )
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        uow.session.add(receipt(None, user, "2", datetime.now(timezone.utc)))
    response = await authenticated_client.get("/usage/me/limits")
    assert response.status_code == 200, response.text
    assert [window["used_percent"] for window in response.json()["windows"]] == expected
    assert response.json()["allowed"] is (cap is None)
