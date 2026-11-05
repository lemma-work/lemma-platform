"""Whether a contact may be answered is read from the total their run is held to.

A surface asks ``contacts_cap_reached`` before every turn for somebody outside
the organization, and past the cap starts no run. The answer comes from the
``contacts_month`` window's running total -- the window every request of that
run is admitted under -- rather than from a sum over the month's ledger, which
grew with the month and ran on every message. These hold its answers at the
cap, and say which statements asking it issues.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
    statements_touching,
)
from app.modules.usage.config import usage_settings
from app.modules.usage.contracts.contacts_cap import contacts_cap_reached
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
    """The statements that add spend up, from the ledger or the counters."""
    return [
        statement
        for statement in statements
        if "sum(" in statement.lower()
        and ("usage_records" in statement or "usage_limit_counters" in statement)
    ]


async def _spend(
    db_manager: DatabaseManager, *, organization_id: UUID, user_id: UUID, cost: str
) -> None:
    """One request of a contact's run, admitted and settled as metering does."""
    gateway = PostgresRequestAccountingGateway(
        SessionUnitOfWorkFactory(db_manager.session_factory),
        MeteringIdentity(
            execution_id=uuid4(),
            organization_id=organization_id,
            user_id=user_id,
            source_type=CONTACT_RUN,
            profile_id="system:counted",
            profile_scope="SYSTEM",
            model_name="counted",
            provider_model_name="counted",
        ),
        CARD,
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


async def _recorded(
    db_manager: DatabaseManager, *, organization_id: UUID, user_id: UUID, cost: str
) -> None:
    """A priced row written past settlement, as the aggregate run recorder
    writes one: no counter has counted it."""
    async with db_manager.session_factory() as session, session.begin():
        session.add(
            UsageRecord(
                organization_id=organization_id,
                user_id=user_id,
                source_type=CONTACT_RUN,
                profile_id="aggregate",
                profile_scope="SYSTEM",
                model_name="aggregate",
                occurred_at=datetime.now(timezone.utc),
                cost_amount=Decimal(cost),
            )
        )


async def _reached(db_manager: DatabaseManager, organization_id: UUID) -> bool:
    """Asked as a surface asks it: in a unit of work of its own, committed."""
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        return await contacts_cap_reached(uow, organization_id=organization_id)


async def _cap(
    client: AsyncClient, organization_id: UUID, monthly_limit_usd: float | None
) -> None:
    response = await client.put(
        f"/usage/organizations/{organization_id}/contacts-cap",
        json={"monthly_limit_usd": monthly_limit_usd},
    )
    assert response.status_code == 200, response.text


async def test_the_default_cap_is_reached_at_its_limit_on_the_running_total(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nobody in the organization set a cap: the deployment's applies.
    monkeypatch.setattr(usage_settings, "usage_contacts_monthly_default_usd", 2.0)
    organization_id, user_id = uuid4(), uuid4()
    # The month's first request counts its window from the ledger.
    await _spend(
        db_manager, organization_id=organization_id, user_id=user_id, cost="1.5"
    )

    with counted_queries() as under:
        assert not await _reached(db_manager, organization_id)
    await _spend(
        db_manager, organization_id=organization_id, user_id=user_id, cost="0.5"
    )
    with counted_queries() as at:
        assert await _reached(db_manager, organization_id)

    for asked in (under, at):
        assert statements_touching(asked, "usage_limit_counters"), format_statements(
            asked
        )
        assert not _sums(asked), format_statements(_sums(asked))
    # The run a surface would have started is refused on the same total.
    with pytest.raises(UsageLimitExceededError) as refused:
        await check_run_budget(
            factory=SessionUnitOfWorkFactory(db_manager.session_factory),
            organization_id=organization_id,
            user_id=user_id,
            profile_scope="SYSTEM",
            outside_audience=True,
        )
    assert refused.value.message == (
        "The organization's monthly cap on answering contacts is reached"
    )


async def test_the_ask_that_counts_the_month_keeps_what_it_counted(
    db_manager: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_settings, "usage_contacts_monthly_default_usd", 2.0)
    organization_id, user_id = uuid4(), uuid4()
    # Spend no counter knows about: the first ask makes the window's counter
    # and counts it from the ledger.
    await _recorded(
        db_manager, organization_id=organization_id, user_id=user_id, cost="1"
    )
    with counted_queries() as first:
        assert not await _reached(db_manager, organization_id)
    # Another write past settlement marks the counter, so the next ask counts
    # the ledger again rather than trusting a total that misses it.
    await _recorded(
        db_manager, organization_id=organization_id, user_id=user_id, cost="1"
    )
    with counted_queries() as recounted:
        assert await _reached(db_manager, organization_id)
    # Held, and the recount kept: asking again sums nothing.
    with counted_queries() as again:
        assert await _reached(db_manager, organization_id)

    assert _sums(first), format_statements(first)
    assert _sums(recounted), format_statements(recounted)
    assert not _sums(again), format_statements(_sums(again))


async def test_a_cap_of_zero_answers_nobody_and_a_removed_cap_limits_nothing(
    authenticated_client: AsyncClient,
    fixed_test_org: dict[str, object],
    fixed_test_user: dict[str, str],
    db_manager: DatabaseManager,
) -> None:
    organization_id = UUID(str(fixed_test_org["id"]))
    await _cap(authenticated_client, organization_id, 0)
    # Nothing spent, and still nobody may be answered.
    assert await _reached(db_manager, organization_id)

    await _cap(authenticated_client, organization_id, None)
    # Well past the deployment's default, which the owner's choice replaces.
    await _spend(
        db_manager,
        organization_id=organization_id,
        user_id=UUID(fixed_test_user["id"]),
        cost="100",
    )
    with counted_queries() as uncapped:
        assert not await _reached(db_manager, organization_id)

    # No limit is no window to read: neither its total nor the ledger.
    assert not statements_touching(uncapped, "usage_limit_counters"), format_statements(
        uncapped
    )
    assert not statements_touching(uncapped, "usage_records"), format_statements(
        uncapped
    )


async def test_a_cap_set_again_after_a_spell_without_one_is_counted_again(
    authenticated_client: AsyncClient,
    fixed_test_org: dict[str, object],
    fixed_test_user: dict[str, str],
    db_manager: DatabaseManager,
) -> None:
    """A request is charged to the month's counter while no cap applies, so a
    cap set back to the value it had finds the total exact, with no recount."""
    organization_id = UUID(str(fixed_test_org["id"]))
    user_id = UUID(fixed_test_user["id"])
    await _cap(authenticated_client, organization_id, 5)
    await _spend(db_manager, organization_id=organization_id, user_id=user_id, cost="1")
    assert not await _reached(db_manager, organization_id)

    await _cap(authenticated_client, organization_id, None)
    await _spend(
        db_manager, organization_id=organization_id, user_id=user_id, cost="10"
    )
    await _cap(authenticated_client, organization_id, 5)

    assert await _reached(db_manager, organization_id)
    # The run's own requests are held to the recounted total too.
    with pytest.raises(UsageLimitExceededError):
        await check_run_budget(
            factory=SessionUnitOfWorkFactory(db_manager.session_factory),
            organization_id=organization_id,
            user_id=user_id,
            profile_scope="SYSTEM",
            outside_audience=True,
        )
