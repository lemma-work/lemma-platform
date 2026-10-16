"""A workflow step reads its wait, its run and its flow once each.

A step is one FAST work item: a transaction on a handler connection that the
next step is queued for, so every statement in it is paid by every step of
every run (``test_workflow_work_load_e2e.py`` measures the total). The resume
path used to find the wait twice, read the run three times and the flow twice,
and re-read the run after updating it; these tests pin the reads per table
rather than a total, so adding a legitimate write elsewhere does not trip them.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import AsyncIterator

import pytest

from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import dispatcher_for, make_due
from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
)
from app.modules.workflow.tests.e2e.workflow_work_support import FlowWork, flow_work

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

_TABLES = ("workflow_run_waits", "workflow_flow_runs", "workflow_flows")
_SELECT_FROM = re.compile(r"^\s*SELECT\b.*?\bFROM (\w+)", re.DOTALL | re.IGNORECASE)

TWO_TIMERS = (
    [
        {"id": "first", "type": "WAIT_UNTIL", "config": {"timeout_seconds": 3600}},
        {"id": "second", "type": "WAIT_UNTIL", "config": {"timeout_seconds": 3600}},
        {"id": "end", "type": "END"},
    ],
    [
        {"id": "e1", "source": "first", "target": "second"},
        {"id": "e2", "source": "second", "target": "end"},
    ],
)


@pytest.fixture
async def flows(
    authenticated_client, fixed_test_org, fixed_test_user, db_manager
) -> AsyncIterator[FlowWork]:
    async with flow_work(
        authenticated_client,
        db_manager,
        org_id=fixed_test_org["id"],
        user_id=fixed_test_user["id"],
    ) as work:
        yield work


def _reads(statements: list[str]) -> Counter[str]:
    tables: Counter[str] = Counter()
    for statement in statements:
        match = _SELECT_FROM.match(statement)
        if match and match.group(1) in _TABLES:
            tables[match.group(1)] += 1
    return tables


async def test_a_timer_step_reads_its_wait_run_and_flow_once(flows: FlowWork) -> None:
    workflow = await flows.workflow(*TWO_TIMERS)
    run = await flows.start(workflow)
    assert run["active_wait"]["wait_type"] == "TIME"
    assert await make_due(flows.sessions, kind="workflow.resume") == 1  # type: ignore[arg-type]

    dispatcher = dispatcher_for(
        flows.sessions,  # type: ignore[arg-type]
        WorkClass.EVENTS,
        registry=flows.registry,
    )
    [item] = await dispatcher.claim_once()
    assert item.kind == "workflow.resume"
    with counted_queries() as statements:
        assert await dispatcher.execute(item) == "done"

    current = await flows.run(run["id"])
    assert current["active_wait"]["node_id"] == "second", current
    assert _reads(statements) == {
        "workflow_run_waits": 1,
        "workflow_flow_runs": 1,
        "workflow_flows": 1,
    }, format_statements(statements, limit=40)
