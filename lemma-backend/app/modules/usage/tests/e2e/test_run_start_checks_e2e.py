"""The check before a run starts reads the totals its requests keep.

``check_run_budget`` refuses a run whose windows are already spent, before a
worker sets it up. It is decided on the windows the run's requests are admitted
under and on their running totals, so these hold it to them from the two
directions in which a run's start differs from one of its requests: whom the
run answers, which is all a start knows of its spend, and a check that refuses
rather than going on to write a journal entry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.test_support.query_counting import counted_queries, format_statements
from app.modules.usage.config import usage_settings
from app.modules.usage.contracts.metering import check_run_budget
from app.modules.usage.domain.accounting import (
    CONTACT_RUN,
    MeteringIdentity,
    RequestReceipt,
    TokenCounts,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.infrastructure.price_catalog import Rate, RateCard
from app.modules.usage.services.request_accounting_gateway import (
    PostgresRequestAccountingGateway,
)

pytestmark = pytest.mark.e2e

CARD = RateCard(
    model="counted",
    enforceable=True,
    rates={
        "input_mtok": Rate(base=Decimal(1)),
        "output_mtok": Rate(base=Decimal(1)),
    },
)


def _sums(statements: list[str]) -> list[str]:
    return [statement for statement in statements if "sum(" in statement.lower()]


async def _spend(
    db_manager: DatabaseManager,
    *,
    organization_id: UUID | None,
    user_id: UUID,
    cost: str,
    source_type: str = "agent_run",
) -> None:
    """One request admitted and settled, as a run's model call is."""
    gateway = PostgresRequestAccountingGateway(
        SessionUnitOfWorkFactory(db_manager.session_factory),
        MeteringIdentity(
            execution_id=uuid4(),
            organization_id=organization_id,
            user_id=user_id,
            source_type=source_type,
            profile_id="system:counted",
            profile_scope="SYSTEM",
            model_name="counted",
            provider_model_name="counted",
        ),
        CARD,
        # What metering hands it: the deployment's settings, patched here.
        usage_settings,
    )
    request_id, now = uuid4(), datetime.now(timezone.utc)
    await gateway.begin(request_id, now)
    await gateway.record(
        RequestReceipt(
            request_id=request_id,
            counts=TokenCounts(input_tokens=1, request_count=1),
            cost=Decimal(cost),
            occurred_at=now,
        )
    )


async def _check(
    db_manager: DatabaseManager,
    *,
    organization_id: UUID | None,
    user_id: UUID,
    outside_audience: bool = False,
) -> None:
    await check_run_budget(
        factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        organization_id=organization_id,
        user_id=user_id,
        profile_scope="SYSTEM",
        outside_audience=outside_audience,
    )


async def test_a_run_for_people_outside_is_held_to_the_organization_not_the_member(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 1.0)
    monkeypatch.setattr(usage_settings, "usage_contacts_monthly_default_usd", 2.0)
    organization_id, user_id = uuid4(), uuid4()
    await _spend(
        db_manager, organization_id=organization_id, user_id=user_id, cost="1.5"
    )

    with pytest.raises(UsageLimitExceededError) as members:
        await _check(db_manager, organization_id=organization_id, user_id=user_id)
    assert members.value.message == "LLM usage limit exceeded for this account"
    # The member who looks after a contact spent their own week. Answering
    # the contact is the organization's spend, which has room.
    await _check(
        db_manager,
        organization_id=organization_id,
        user_id=user_id,
        outside_audience=True,
    )

    await _spend(
        db_manager,
        organization_id=organization_id,
        user_id=user_id,
        cost="2",
        source_type=CONTACT_RUN,
    )
    with (
        counted_queries() as statements,
        pytest.raises(UsageLimitExceededError) as contacts,
    ):
        await _check(
            db_manager,
            organization_id=organization_id,
            user_id=user_id,
            outside_audience=True,
        )
    assert contacts.value.message == (
        "The organization's monthly cap on answering contacts is reached"
    )
    assert not _sums(statements), format_statements(statements)


async def test_a_refused_run_start_keeps_the_recount_it_made(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_weekly_limit_usd", 0.1)
    user_id = uuid4()
    # Counts the new window: nothing spent in it yet.
    await _check(db_manager, organization_id=None, user_id=user_id)
    # An ordinary ORM insert, as the aggregate run recorder makes, has the
    # counter recounted before anything trusts it again.
    async with db_manager.session_factory() as session, session.begin():
        session.add(
            UsageRecord(
                user_id=user_id,
                source_type="agent_run",
                profile_id="aggregate",
                profile_scope="SYSTEM",
                model_name="aggregate",
                occurred_at=datetime.now(timezone.utc),
                cost_amount=Decimal("0.2"),
            )
        )

    with pytest.raises(UsageLimitExceededError):
        await _check(db_manager, organization_id=None, user_id=user_id)
    # Refusing the run again should not cost another sum over the ledger.
    with counted_queries() as statements, pytest.raises(UsageLimitExceededError):
        await _check(db_manager, organization_id=None, user_id=user_id)

    assert not _sums(statements), format_statements(statements)
