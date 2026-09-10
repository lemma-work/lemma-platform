"""A ledger's exact amount takes precedence over its compatibility float."""

from decimal import Decimal

from sqlalchemy import Numeric, cast, func
from sqlalchemy.sql.elements import ColumnElement

from app.modules.usage.infrastructure.models import UsageRecord


def recorded_cost() -> ColumnElement[Decimal]:
    # Cast each legacy amount before SUM; casting a float sum preserves its error.
    return func.coalesce(
        UsageRecord.cost_amount, cast(UsageRecord.cost_usd, Numeric(24, 9))
    )
