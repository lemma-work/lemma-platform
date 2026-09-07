from datetime import datetime, timezone
from decimal import Decimal

from app.modules.usage.contracts import ModelPricing

from app.modules.usage.domain.accounting import TokenCounts
from app.modules.usage.infrastructure.price_catalog import (
    Rate,
    RateCard,
    resolve_rate_card,
)


def test_batch_cost_is_sum_of_request_prices_not_price_of_batch_tokens() -> None:
    card = RateCard(
        model="tiered",
        rates={"input_mtok": Rate(base=Decimal("1"), tiers=((100, Decimal("2")),))},
    )
    request = TokenCounts(input_tokens=75)
    request_cost = card.price(request)
    assert request_cost is not None
    assert request_cost + request_cost == Decimal("0.000150")
    assert card.price(TokenCounts(input_tokens=150)) == Decimal("0.000300")


def test_known_provider_prices_automatically_but_private_gateway_is_only_an_estimate() -> (
    None
):
    known = resolve_rate_card(
        {
            "provider_model_name": "claude-sonnet-4-5",
            "protocol": "ANTHROPIC_COMPATIBLE",
        },
        {},
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    unknown = resolve_rate_card(
        {
            "provider_model_name": "claude-sonnet-4-5",
            "config": {"base_url": "https://gateway.example"},
        },
        {},
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert known.priceable
    assert not unknown.priceable
    counts = TokenCounts(input_tokens=1000, output_tokens=100)
    assert known.price(counts) == unknown.price(counts) == Decimal(".0045")


def test_missing_rates_cannot_turn_even_empty_usage_into_a_free_request() -> None:
    card = resolve_rate_card(
        {"provider_model_name": "unlisted-test-model"},
        {},
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert not card.priceable
    assert card.price(TokenCounts()) is None
    assert card.price(TokenCounts(input_tokens=1000, output_tokens=100)) is None


def test_explicit_zero_price_is_known_and_priceable() -> None:
    card = RateCard(
        model="free",
        enforceable=True,
        rates={
            "input_mtok": Rate(base=Decimal(0)),
            "output_mtok": Rate(base=Decimal(0)),
        },
    )
    assert card.price(TokenCounts(input_tokens=10)) == 0
    assert card.priceable


def test_cache_buckets_are_inclusive_not_additional_input() -> None:
    card = RateCard(
        model="cached",
        rates={
            "input_mtok": Rate(base=Decimal("1")),
            "cache_read_mtok": Rate(base=Decimal(".1")),
            "cache_write_mtok": Rate(base=Decimal("2")),
        },
    )
    assert card.price(
        TokenCounts(input_tokens=1000, cache_read_tokens=800, cache_write_tokens=100)
    ) == Decimal(".00038")


def test_audio_receipts_use_audio_rates() -> None:
    card = RateCard(
        model="audio",
        enforceable=True,
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


def test_sonnet_text_receipt_does_not_charge_for_unused_native_search() -> None:
    card = resolve_rate_card(
        {
            "provider_model_name": "claude-sonnet-4-5",
            "protocol": "ANTHROPIC_COMPATIBLE",
        },
        {},
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    # Listing a native-search rate does not charge for a search that never ran.
    assert card.price(
        TokenCounts(input_tokens=1000, output_tokens=100, request_count=1)
    ) == Decimal(".0045")


def test_gemini_pricing_does_not_require_context_metadata() -> None:
    card = resolve_rate_card(
        {
            "provider_model_name": "gemini-2.5-pro",
            "config": {
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"
            },
        },
        {},
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert card.enforceable
    assert card.priceable
    assert card.price(TokenCounts(input_tokens=1000, output_tokens=100)) == Decimal(
        ".00225"
    )


def test_missing_output_price_is_not_a_free_output_price() -> None:
    card = RateCard(
        model="incomplete",
        enforceable=True,
        rates={"input_mtok": Rate(base=Decimal("1"))},
    )
    assert card.price(TokenCounts(input_tokens=10, output_tokens=1)) is None
    assert card.price(TokenCounts(input_tokens=10)) == Decimal(".00001")
    assert not card.priceable


def test_unpriced_audio_and_cache_receipts_do_not_look_like_zero_cost() -> None:
    card = RateCard(
        model="text-only-rates",
        rates={
            "input_mtok": Rate(base=Decimal("1")),
            "output_mtok": Rate(base=Decimal("1")),
        },
    )
    assert card.price(TokenCounts(input_tokens=10, input_audio_tokens=5)) is None
    assert card.price(TokenCounts(input_tokens=10, cache_read_tokens=5)) is None


def test_registered_custom_gateway_prices_without_context_metadata() -> None:
    card = resolve_rate_card(
        {
            "provider_model_name": "gpt-4o",
            "config": {"base_url": "https://gateway.example"},
        },
        {"gpt-4o": ModelPricing(1, 2)},
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert card.priceable
    assert card.price(TokenCounts(input_tokens=1000, output_tokens=100)) == Decimal(
        ".0012"
    )


def test_receipt_tiers_use_actual_input_and_start_only_above_threshold() -> None:
    card = RateCard(
        model="tiered",
        rates={
            "input_mtok": Rate(base=Decimal("1"), tiers=((100, Decimal("2")),)),
            "output_mtok": Rate(base=Decimal("3"), tiers=((100, Decimal("6")),)),
        },
    )
    assert card.price(TokenCounts(input_tokens=100, output_tokens=10)) == Decimal(
        ".00013"
    )
    assert card.price(TokenCounts(input_tokens=101, output_tokens=10)) == Decimal(
        ".000262"
    )
