"""The cross-pod assertion the query plan makes possible.

Ad-hoc SQL runs as one shared role holding SELECT on every pod's schema, so
pod isolation rests on the ``search_path`` and on ``analyze_query`` refusing a
schema-qualified reference. That is one control, and a parser gap in it is
cross-tenant exposure rather than a degraded error. The plan is PostgreSQL's
own statement of which relations it resolved, so it can be checked.

Real ``EXPLAIN (FORMAT JSON, VERBOSE)`` shapes, trimmed to the keys under test.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.domain.errors import DatastoreQueryError
from app.modules.datastore.infrastructure.record_query_cost import (
    _reject_other_pods,
    _schemas_in,
)

OUR_POD = "pod_11111111_1111_1111_1111_111111111111"
ANOTHER_POD = "pod_22222222_2222_2222_2222_222222222222"


def _seq_scan(schema: str, relation: str) -> dict:
    return {
        "Node Type": "Seq Scan",
        "Schema": schema,
        "Relation Name": relation,
        "Alias": relation,
        "Output": [f"{relation}.id"],
        "Total Cost": 12.0,
        "Plan Rows": 3,
    }


def test_a_plan_confined_to_this_pod_is_allowed():
    plan = [{"Plan": {"Node Type": "Aggregate", "Plans": [_seq_scan(OUR_POD, "t")]}}]

    _reject_other_pods(plan, OUR_POD)


def test_a_plan_reaching_into_another_pod_is_refused():
    plan = [
        {
            "Plan": {
                "Node Type": "Hash Join",
                "Plans": [
                    _seq_scan(OUR_POD, "orders"),
                    _seq_scan(ANOTHER_POD, "salaries"),
                ],
            }
        }
    ]

    with pytest.raises(DatastoreQueryError, match="outside this pod"):
        _reject_other_pods(plan, OUR_POD)


def test_the_refusal_does_not_echo_the_other_pod_back():
    """Reaching here means a control was bypassed; do not confirm what was hit."""
    plan = [{"Plan": _seq_scan(ANOTHER_POD, "salaries")}]

    with pytest.raises(DatastoreQueryError) as raised:
        _reject_other_pods(plan, OUR_POD)

    assert ANOTHER_POD not in str(raised.value)
    assert "salaries" not in str(raised.value)


def test_a_scan_buried_under_a_key_that_is_not_plans_is_still_seen():
    """Subplans and CTEs hang off keys that have moved between releases.

    A node the walk does not visit is a node this check does not protect, so
    it walks the document rather than the ``Plans`` links.
    """
    plan = [
        {
            "Plan": {
                "Node Type": "Result",
                "Subplans": [{"Plan": _seq_scan(ANOTHER_POD, "salaries")}],
            }
        }
    ]

    with pytest.raises(DatastoreQueryError):
        _reject_other_pods(plan, OUR_POD)


@pytest.mark.parametrize("schema", ["pg_catalog", "public", "information_schema"])
def test_the_check_has_no_opinion_about_non_pod_namespaces(schema):
    """Narrow on purpose: those are the parser's and get_tables' business, and
    an allow-list here would refuse legitimate queries for a different reason."""
    plan = [{"Plan": _seq_scan(schema, "pg_class")}]

    _reject_other_pods(plan, OUR_POD)


def test_a_plan_with_no_schema_anywhere_is_not_a_violation():
    plan = [
        {"Plan": {"Node Type": "Function Scan", "Function Name": "generate_series"}}
    ]

    assert _schemas_in(plan) == set()
    _reject_other_pods(plan, OUR_POD)
