"""What the planner is asked before an ad-hoc query is allowed to run.

One ``EXPLAIN`` answers two questions, and both of them are refusals.

*Is it affordable?* One unbounded ``SELECT`` against a large pod table can hold
a connection and a server-side cursor for as long as it takes to stream, and
the caller usually meant to add a filter.

*Does it stay inside this pod?* Ad-hoc SQL runs as one shared role that holds
SELECT on every pod's schema (see ``query_role``), so what keeps pod A's query
out of pod B's tables is the ``search_path`` plus ``analyze_query`` rejecting
schema-qualified references. That makes a single parser gap — a relation form
sqlglot does not model as ``exp.Table``, a dialect change, an inheritance or
``regclass`` trick — the difference between a degraded error and cross-tenant
exposure. The plan is the one place PostgreSQL states which relations it
resolved, by its own rules rather than the parser's, so it is where that can be
checked instead of assumed.

The namespace check is deliberately narrow: it refuses a plan naming some other
pod's schema, and says nothing about ``pg_catalog``, ``public`` or the
unqualified nodes (subqueries, CTEs, function scans) that carry no schema at
all. Those are already handled — the parser refuses qualified names and
``get_tables`` refuses unregistered ones — and widening this to an allow-list
would start rejecting legitimate queries for a boundary it is not about.
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

#: What ``EXPLAIN (FORMAT JSON)`` hands back: a one-element array holding the
#: plan tree. Typed as the JSON document it is rather than modelled node by
#: node — three keys of it are read here, and PostgreSQL adds and moves the
#: rest between releases.
type PlanValue = (
    str | int | float | bool | None | list[PlanValue] | dict[str, PlanValue]
)
type PlanDocument = list[dict[str, PlanValue]]

#: Pod schemas are named ``pod_<uuid with underscores>`` by
#: ``SchemaManager._get_schema_name``; this is that prefix, and the only
#: namespace family the cross-pod check has an opinion about.
_POD_SCHEMA_PREFIX = "pod_"


async def guard_query_plan(session, query: str, *, schema_name: str) -> None:
    """Plan an ad-hoc query and refuse it on either ground the plan can show.

    ``EXPLAIN`` (without ``ANALYZE``) only plans the statement, so this runs no
    user SQL; it executes under the same RLS context and ``search_path``, so
    both the estimates and the resolved relations are the ones the real
    execution would use.

    The cost estimate is the planner's, which is why the listing index matters
    here and not only for speed: with no index the planner costs a sequential
    scan, so past a certain row count a perfectly reasonable query is refused
    rather than run slowly.
    """
    plan_json = await _explain(session, query)
    _reject_other_pods(plan_json, schema_name)
    _reject_expensive(plan_json)


async def _explain(session, query: str) -> PlanDocument:
    """The plan, as PostgreSQL describes it.

    ``VERBOSE`` for one field: without it the JSON plan names relations but not
    their schemas, and the schema is the whole point of ``_reject_other_pods``.
    It costs nothing at plan time and no extra round trip.
    """
    try:
        explain = await session.execute(text(f"EXPLAIN (FORMAT JSON, VERBOSE) {query}"))
        plan_json = explain.scalar_one()
    except DBAPIError as exc:
        logger.debug("datastore.record.query_plan.propagated", exc_info=True)
        raise_record_read_error(exc, operation="query planning")

    if isinstance(plan_json, str):
        plan_json = json.loads(plan_json)
    return plan_json


def _schemas_in(node: PlanValue) -> set[str]:
    """Every namespace the plan says it resolved a relation in.

    Walks the whole tree rather than the ``Plans`` links alone: subplans,
    initplans and CTE nodes hang off keys that have changed across releases,
    and a scan this check failed to visit is a scan it does not protect.
    """
    if isinstance(node, dict):
        found = {node["Schema"]} if isinstance(node.get("Schema"), str) else set()
        return found.union(*(_schemas_in(value) for value in node.values()), set())
    if isinstance(node, list):
        return set().union(*(_schemas_in(item) for item in node), set())
    return set()


def _reject_other_pods(plan_json: PlanDocument, schema_name: str) -> None:
    """Refuse a plan that resolved a relation in a different pod's schema.

    Reaching here means the parser passed a reference it should not have, so
    the refusal is deliberately blunt and says nothing about what was named.
    """
    foreign = {
        schema
        for schema in _schemas_in(plan_json)
        if schema.startswith(_POD_SCHEMA_PREFIX) and schema != schema_name
    }
    if not foreign:
        return
    logger.warning(
        "datastore.record.query_plan_left_the_pod_schema.degraded",
        schema_name=schema_name,
        foreign_schema_count=len(foreign),
    )
    raise DatastoreQueryError(
        "Query rejected: it reads data outside this pod. Reference this pod's "
        "tables by their bare name."
    )


def _reject_expensive(plan_json: PlanDocument) -> None:
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
