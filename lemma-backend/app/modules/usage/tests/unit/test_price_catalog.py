from datetime import datetime, timezone
from decimal import Decimal

from app.modules.usage.domain.accounting import CostSource, TokenCounts
from app.modules.usage.infrastructure.price_catalog import (
    Rate,
    RateCard,
    resolve_rate_card,
)


def test_batch_cost_is_sum_of_request_prices_not_price_of_batch_tokens() -> None:
    card = RateCard(
        model="tiered",
        source=CostSource.REGISTERED,
        rates={"input_mtok": Rate(base=Decimal("1"), tiers=((100, Decimal("2")),))},
    )
    request = TokenCounts(input_tokens=75)
    request_cost = card.price(request)
    assert request_cost is not None
    assert request_cost + request_cost == Decimal("0.000150")
    assert card.price(request.plus(request)) == Decimal("0.000300")


def test_known_provider_prices_automatically_but_private_gateway_is_only_an_estimate() -> (
    None
):
    known = resolve_rate_card(
        {
            "provider_model_name": "claude-sonnet-4-5",
            "protocol": "ANTHROPIC_COMPATIBLE",
        },
        {},
        datetime.now(timezone.utc),
    )
    unknown = resolve_rate_card(
        {
            "provider_model_name": "claude-sonnet-4-5",
            "config": {"base_url": "https://gateway.example"},
        },
        {},
        datetime.now(timezone.utc),
    )
    assert known.source == CostSource.ESTIMATED
    assert known.enforceable and known.input_ceiling
    assert unknown.source == CostSource.ESTIMATED
    assert not unknown.enforceable


def test_zero_price_is_known_and_has_a_zero_request_bound() -> None:
    card = RateCard(
        model="free",
        source=CostSource.REGISTERED,
        enforceable=True,
        input_ceiling=1000,
        rates={
            "input_mtok": Rate(base=Decimal(0)),
            "output_mtok": Rate(base=Decimal(0)),
        },
    )
    assert card.price(TokenCounts(input_tokens=10)) == 0
    assert card.bound(100) == 0


def test_request_bound_includes_cache_write_premium_and_output_tier() -> None:
    card = RateCard(
        model="premium",
        source=CostSource.REGISTERED,
        enforceable=True,
        input_ceiling=1000,
        rates={
            "input_mtok": Rate(base=Decimal(1)),
            "cache_write_mtok": Rate(base=Decimal(2)),
            "output_mtok": Rate(base=Decimal(3), tiers=((100, Decimal(6)),)),
        },
    )
    assert card.bound(500) == Decimal("0.005")


def test_cache_buckets_are_inclusive_not_additional_input() -> None:
    card = RateCard(
        model="cached",
        source=CostSource.REGISTERED,
        rates={
            "input_mtok": Rate(base=Decimal("1")),
            "cache_read_mtok": Rate(base=Decimal(".1")),
            "cache_write_mtok": Rate(base=Decimal("2")),
        },
    )
    assert card.price(
        TokenCounts(input_tokens=1000, cache_read_tokens=800, cache_write_tokens=100)
    ) == Decimal(".00038")


def test_audio_subsets_use_their_own_rates_and_bounds() -> None:
    card = RateCard(
        model="audio",
        source=CostSource.REGISTERED,
        enforceable=True,
        input_ceiling=1000,
        rates={
            "input_mtok": Rate(base=Decimal("1")),
            "input_audio_mtok": Rate(base=Decimal("10")),
            "output_mtok": Rate(base=Decimal("2")),
            "output_audio_mtok": Rate(base=Decimal("20")),
        },
    )
    assert card.price(
        TokenCounts(
            input_tokens=100,
            input_audio_tokens=50,
            output_tokens=10,
            output_audio_tokens=5,
        )
    ) == Decimal(".00066")
    assert card.bound(100) == Decimal(".012")
