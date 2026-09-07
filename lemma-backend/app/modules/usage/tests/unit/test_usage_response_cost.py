"""Request detail and aggregate views must read the same authoritative cost."""

from decimal import Decimal
from uuid import uuid4

from app.modules.usage.api.controllers import _record_response
from app.modules.usage.domain.entities import UsageRecord


def test_response_uses_decimal_cost_and_preserves_unknown() -> None:
    record = UsageRecord(
        user_id=uuid4(),
        profile_id="system:test",
        profile_scope="SYSTEM",
        model_name="test",
        cost_amount=Decimal("0.000000001"),
        cost_usd=4,
    )
    assert _record_response(record).cost_usd == 0.000000001
    record.cost_amount = None
    assert _record_response(record).cost_usd == 4
    record.cost_usd = None
    assert _record_response(record).cost_usd is None
    record.cost_amount = Decimal(0)
    assert _record_response(record).cost_usd == 0
