"""The token-and-cost columns every usage aggregate selects, in one place.

The summary and the time-series read the same numbers off the same table and
differ only in what they group by, so the column list and the row-to-dict
mapping live here rather than being written twice and drifting once.

Two of these need explaining:

``system_cost_usd`` filters on ``profile_scope``. Cost is now resolved for every
scope -- a runtime profile someone added with their own key reports what it spent
-- but only spend on the deployment's own credentials is what a plan limit is
measured against. Summing the two together would show an organization a bill it
does not owe, so they are separate columns all the way out to the API.

Both use ``COALESCE``: an unpriced model meters tokens with a null cost, and a
null summed into the total would erase every priced row in the same bucket.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import case, func
from sqlalchemy.sql.elements import ColumnElement

from app.modules.usage.domain.entities import UsageProfileScope
from app.modules.usage.infrastructure.models import UsageRecord as UsageRecordModel

_SYSTEM = UsageProfileScope.SYSTEM.value


class AggregateRow(Protocol):
    """The columns `token_and_cost_columns` selects, as a result row exposes them.

    A structural type rather than `Any`: the mapping below reads seven attributes
    off a row it did not construct, and naming them is what makes a renamed label
    a type error instead of an `AttributeError` in a request.
    """

    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    units: float | None
    system_cost_usd: float | None
    total_cost_usd: float | None


def token_and_cost_columns(
    *, with_record_count: bool = False
) -> list[ColumnElement[object]]:
    """The aggregate columns shared by the summary and the time-series."""
    columns: list[ColumnElement[object]] = [
        func.sum(UsageRecordModel.input_tokens).label("input_tokens"),
        func.sum(UsageRecordModel.output_tokens).label("output_tokens"),
        func.sum(UsageRecordModel.cached_input_tokens).label("cached_input_tokens"),
        func.sum(UsageRecordModel.cache_write_tokens).label("cache_write_tokens"),
        func.sum(UsageRecordModel.units).label("units"),
        func.coalesce(func.sum(UsageRecordModel.cost_usd), 0.0).label("total_cost_usd"),
        func.coalesce(
            func.sum(
                case(
                    (
                        UsageRecordModel.profile_scope == _SYSTEM,
                        UsageRecordModel.cost_usd,
                    ),
                    else_=0.0,
                )
            ),
            0.0,
        ).label("system_cost_usd"),
    ]
    if with_record_count:
        columns.append(func.count().label("record_count"))
    return columns


def aggregate_row_to_dict(
    row: AggregateRow, *, with_record_count: bool = False
) -> dict[str, int | float]:
    """One aggregate row as the shape the API and the summary entity expect."""
    input_tokens = int(row.input_tokens or 0)
    output_tokens = int(row.output_tokens or 0)
    cached = int(row.cached_input_tokens or 0)
    cache_write = int(row.cache_write_tokens or 0)
    totals: dict[str, int | float] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cached_input_tokens": cached,
        "cache_write_tokens": cache_write,
        "uncached_input_tokens": max(0, input_tokens - cached - cache_write),
        "units": float(row.units or 0.0),
        "system_cost_usd": float(row.system_cost_usd or 0.0),
        "total_cost_usd": float(row.total_cost_usd or 0.0),
    }
    if with_record_count:
        totals["record_count"] = int(row.record_count or 0)
    return totals
