"""A paid request that is not a model call is recorded like one."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.usage.contracts import ModelPricing
from app.modules.usage.domain.errors import UsageContextMissingError
from app.modules.usage.infrastructure.price_catalog import resolve_rate_card
from app.modules.usage.services.external_request import (
    ExternalRequestOutcome,
    metered_external_request,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PRICED = resolve_rate_card(
    {"model_name": "typesafe:jev", "provider_model_name": "jev"},
    {"typesafe:jev": ModelPricing(2.0, 0.0)},
    NOW,
)
UNPRICED = resolve_rate_card({"model_name": "typesafe:jev"}, {}, NOW)


def test_a_settled_request_is_priced_by_what_the_provider_reported() -> None:
    outcome = ExternalRequestOutcome()
    outcome.settle(input_tokens=500_000)

    receipt = outcome.receipt(uuid4(), NOW, PRICED)

    assert receipt.cost == Decimal("1.000000000")
    assert (receipt.counts.input_tokens, receipt.counts.request_count) == (500_000, 1)
    assert receipt.counts.unpriced_requests == 0


def test_a_refused_request_costs_nothing() -> None:
    outcome = ExternalRequestOutcome()
    outcome.reject()

    receipt = outcome.receipt(uuid4(), NOW, PRICED)

    assert receipt.cost == Decimal(0)
    assert receipt.counts.request_count == 1


def test_a_request_with_no_usage_reported_is_unconfirmed() -> None:
    receipt = ExternalRequestOutcome().receipt(uuid4(), NOW, PRICED)

    assert receipt.cost is None
    assert receipt.counts.unconfirmed_requests == 1


def test_a_request_with_no_price_is_recorded_unpriced() -> None:
    outcome = ExternalRequestOutcome()
    outcome.settle(input_tokens=10)

    receipt = outcome.receipt(uuid4(), NOW, UNPRICED)

    assert receipt.cost is None
    assert receipt.counts.unpriced_requests == 1


async def test_metering_outside_an_execution_is_refused() -> None:
    with pytest.raises(UsageContextMissingError):
        async with metered_external_request({"model_name": "typesafe:jev"}):
            pass
