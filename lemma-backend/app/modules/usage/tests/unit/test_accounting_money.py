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
