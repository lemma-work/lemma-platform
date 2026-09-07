from decimal import Decimal

import pytest

from app.modules.usage.domain.accounting import money


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.01"])
def test_money_rejects_invalid_allowance(value: str) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        money(value)


def test_money_preserves_subcent_spend_without_rounding_authority_down() -> None:
    assert money("0.0000000011") == Decimal("0.000000002")
    assert money("0.1") + money("0.2") == money("0.3")


def test_repeated_nanodollar_charges_remain_exact() -> None:
    total = sum((money("0.000000001") for _ in range(10000)), Decimal(0))
    assert total == Decimal("0.000010000")


def test_money_retains_all_database_digits() -> None:
    assert money("999999999999999.999999999") == Decimal("999999999999999.999999999")


def test_legacy_pricing_keeps_a_nanodollar_charge() -> None:
    from app.modules.usage.contracts import ModelPricing
    from app.modules.usage.services.pricing import UsagePricing

    class TinyPrice(UsagePricing):
        _SYSTEM_MODEL_PRICING = {"tiny": ModelPricing(0.001, 0.001)}

    cost, missing = TinyPrice()._calculate_system_cost(
        profile_scope="SYSTEM",
        model_name="tiny",
        provider_model_name=None,
        input_tokens=1,
        output_tokens=0,
        units=0,
    )
    assert cost == Decimal("0.000000001")
    assert not missing


def test_legacy_pricing_rounds_once_after_adding_token_categories() -> None:
    from app.modules.usage.contracts import ModelPricing
    from app.modules.usage.services.pricing import UsagePricing

    class TinyPrice(UsagePricing):
        _SYSTEM_MODEL_PRICING = {"tiny": ModelPricing(0.0004, 0.0004)}

    cost, missing = TinyPrice()._calculate_system_cost(
        profile_scope="SYSTEM",
        model_name="tiny",
        provider_model_name=None,
        input_tokens=1,
        output_tokens=1,
        units=0,
    )
    assert cost == Decimal("0.000000001")
    assert not missing
