"""Refuse an ad-hoc query the planner says will be expensive.

This is a guard, not an optimization: one unbounded ``SELECT`` against a large
pod table can hold a connection and a server-side cursor for as long as it
takes to stream, and the caller usually meant to add a filter.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import DatastoreQueryError
from app.modules.datastore.infrastructure.record_errors import raise_record_read_error

logger = get_logger(__name__)


async def reject_if_too_expensive(session, query: str) -> None:
    """Reject a query whose planned cost or row estimate exceeds the ceiling.

    ``EXPLAIN`` (without ``ANALYZE``) only plans the statement, so this runs no
    user SQL; it executes under the same RLS context, so estimates reflect the
    row-filtered query.

    The estimate is the planner's, which is why the listing index matters here
    and not only for speed: with no index the planner costs a sequential scan,
    so past a certain row count a perfectly reasonable query is refused rather
    than run slowly.
    """
    try:
        explain = await session.execute(text(f"EXPLAIN (FORMAT JSON) {query}"))
        plan_json = explain.scalar_one()
    except DBAPIError as exc:
        logger.debug("datastore.record.query_plan.propagated", exc_info=True)
        raise_record_read_error(exc, operation="query planning")

    if isinstance(plan_json, str):
        plan_json = json.loads(plan_json)
    plan = plan_json[0]["Plan"]
    total_cost = float(plan.get("Total Cost", 0.0))
    plan_rows = int(plan.get("Plan Rows", 0))
    if (
        total_cost > datastore_settings.datastore_query_max_cost
        or plan_rows > datastore_settings.datastore_query_max_plan_rows
    ):
        raise DatastoreQueryError(
            "Query rejected: its estimated cost is too high. "
            "Add filters or a LIMIT to narrow the result set."
        )
