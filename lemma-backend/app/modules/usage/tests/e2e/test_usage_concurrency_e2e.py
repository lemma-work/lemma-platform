from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.domain.ports import UsageLimitValues
from app.modules.usage.infrastructure.models import UsageLimitCounter
from app.modules.usage.infrastructure.repositories import UsageRepository
from app.modules.usage.services.usage_service import ModelPricing, UsageService

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _system_model_metadata():
    UsageService.register_model_pricing({"test-model": ModelPricing(1.0, 0.0)})
    yield
    UsageService._SYSTEM_MODEL_PRICING.pop("test-model", None)


class _Limits:
    def __init__(self, *, organization: float | None, user: float) -> None:
        self.values = UsageLimitValues(
            org_monthly_limit_usd=organization,
            user_weekly_limit_usd=user,
            user_monthly_limit_usd=None,
            user_limit_scope="organization",
        )

    async def resolve_limits(self, *, organization_id, user_id):
        del organization_id, user_id
        return self.values


async def _reserve(
    factory: SessionUnitOfWorkFactory,
    *,
    user_id: UUID,
    organization_id: UUID | None,
    limits: _Limits,
    now: datetime,
) -> bool:
    try:
        async with factory() as uow:
            service = UsageService(
                usage_repository=UsageRepository(uow),
                usage_limit_port=limits,
            )
            await service.reserve_for_profile(
                organization_id=organization_id,
                user_id=user_id,
                profile_id="system:lemma",
                profile_scope="SYSTEM",
                model_name="test-model",
                now=now,
            )
        return True
    except UsageLimitExceededError:
        return False


async def test_concurrent_fresh_window_never_admits_above_exact_limit(db_manager):
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id = uuid4()
    now = datetime(2026, 7, 9, 12, tzinfo=timezone.utc)
    results = await asyncio.gather(
        *(
            _reserve(
                factory,
                user_id=user_id,
                organization_id=None,
                limits=_Limits(organization=None, user=0.05),
                now=now,
            )
            for _ in range(20)
        )
    )

    assert sum(results) == 5
    async with db_manager.session_factory() as session:
        counters = list(
            (
                await session.scalars(
                    select(UsageLimitCounter).where(
                        UsageLimitCounter.user_id == user_id
                    )
                )
            ).all()
        )
    assert len(counters) == 1
    assert counters[0].reserved_usd == pytest.approx(0.05)


async def test_rejected_multi_scope_reservation_rolls_back_every_scope(db_manager):
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id = uuid4()
    organization_id = uuid4()
    now = datetime(2026, 7, 9, 12, tzinfo=timezone.utc)
    limits = _Limits(organization=0.02, user=0.01)

    assert await _reserve(
        factory,
        user_id=user_id,
        organization_id=organization_id,
        limits=limits,
        now=now,
    )
    assert not await _reserve(
        factory,
        user_id=user_id,
        organization_id=organization_id,
        limits=limits,
        now=now,
    )

    async with db_manager.session_factory() as session:
        counters = list((await session.scalars(select(UsageLimitCounter))).all())
    assert len(counters) == 2
    assert all(counter.reserved_usd == pytest.approx(0.01) for counter in counters)
