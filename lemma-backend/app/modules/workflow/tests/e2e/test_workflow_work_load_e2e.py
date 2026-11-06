"""Load: many three-step workflow runs across many pods on one work runtime.

Each run is FUNCTION (a loopback runtime that accepts and calls back) -> TIME
(one second) -> DECISION (a fixed answer) -> end, started by ``workflow.start``
items the way a burst of schedule fires starts them. One ``WorkRuntime`` --
the worker's own dispatch loops, its own sized pool -- runs everything.

Measured, and printed as one JSON line (``WORKFLOW_LOAD_RESULT``): runs per
second, per-step latency percentiles, end-to-end run latency, and the peak
number of database connections -- the runtime's own (held to its budget) and
the whole database's. Sized by ``WORKFLOW_LOAD_RUNS`` and
``WORKFLOW_LOAD_PODS``; the default is small enough for the protected lane, and
``WORKFLOW_LOAD_RUNS=10000 WORKFLOW_LOAD_PODS=100`` is the full measurement.
"""

from __future__ import annotations

import json
import os
import time
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.infrastructure.work.db import DbBudget
from app.core.infrastructure.work.runtime import WorkRuntime
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workflow.tests.e2e.workflow_work_support import (
    CLASSES,
    FlowWork,
    flow_work,
)

# drives_work: the runtime below must be the only one claiming. A session
# worker left running took `workflow.start` items too and ran their functions
# for real instead of through the loopback runtime, so those runs never
# finished and the wait ran into the 300s test timeout.
pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.drives_work]

RUNS = int(os.environ.get("WORKFLOW_LOAD_RUNS", "400"))
RUNTIME_APP = "lemma-work-load-runtime"
PODS = int(os.environ.get("WORKFLOW_LOAD_PODS", "8"))

THREE_STEPS_EDGES = [
    {"id": "e1", "source": "fetch", "target": "pause"},
    {"id": "e2", "source": "pause", "target": "triage"},
    {"id": "e3", "source": "triage", "target": "end"},
]


def _nodes(function_name: str) -> list[dict]:
    return [
        {
            "id": "fetch",
            "type": "FUNCTION",
            "config": {"function_name": function_name, "input_mapping": {}},
        },
        {"id": "pause", "type": "WAIT_UNTIL", "config": {"timeout_seconds": 1}},
        {
            "id": "triage",
            "type": "DECISION",
            "config": {
                "question": {
                    "instruction": "Is this fine?",
                    "evidence": {"type": "expression", "value": "fetch"},
                    "answer": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": "Is it fine?",
                    },
                    "routes": {"yes": "end", "no": "end"},
                }
            },
        },
        {"id": "end", "type": "END"},
    ]


async def _new_pod(flows: FlowWork, org_id: str) -> str:
    response = await flows.client.post(
        "/pods",
        json={
            "name": f"load {len(org_id)}-{time.monotonic_ns()}",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _enqueue_starts(flows: FlowWork, flows_by_pod: dict[str, UUID]) -> None:
    """``workflow.start`` items, as a burst of fires writes them."""
    per_pod = RUNS // len(flows_by_pod)
    async with flows.sessions() as session:  # type: ignore[operator]
        for pod_id, flow_id in flows_by_pod.items():
            ns = f"pod:{pod_id}"
            for cls, cap in (("events", 16), ("automation", 32), ("delivery", 8)):
                await session.execute(
                    text(
                        "INSERT INTO work_queues (class, ns, max_running) "
                        "VALUES (:cls, :ns, :cap) ON CONFLICT DO NOTHING"
                    ),
                    {"cls": cls, "ns": ns, "cap": cap},
                )
            await session.execute(
                text(
                    "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key) "
                    "SELECT gen_random_uuid(), :ns, 'events', 'workflow.start', "
                    "jsonb_build_object('flow_id', CAST(:flow AS text), "
                    "'user_id', CAST(:user AS text), 'run_id', r::text), "
                    "'workflow-start:' || r::text "
                    "FROM (SELECT gen_random_uuid() AS r "
                    "FROM generate_series(1, :n)) AS runs"
                ),
                {
                    "ns": ns,
                    "flow": str(flow_id),
                    "user": str(flows.user_id),
                    "n": per_pod,
                },
            )
        await session.commit()


async def _percentiles(flows: FlowWork, sql: str) -> dict[str, float]:
    row = await flows.scalar(
        "SELECT json_build_object("
        "'p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY s), "
        "'p95', percentile_cont(0.95) WITHIN GROUP (ORDER BY s), "
        "'p99', percentile_cont(0.99) WITHIN GROUP (ORDER BY s), "
        "'max', max(s), 'n', count(*)) FROM (" + sql + ") t"
    )
    assert isinstance(row, dict)
    return {key: round(float(value or 0), 3) for key, value in row.items()}


async def test_thousands_of_three_step_runs_across_pods(
    e2e_settings,
    db_manager,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
) -> None:
    async with flow_work(
        authenticated_client,
        db_manager,
        org_id=fixed_test_org["id"],
        user_id=fixed_test_user["id"],
    ) as flows:
        flows_by_pod: dict[str, UUID] = {}
        for _ in range(PODS):
            flows.pod_id = await _new_pod(flows, fixed_test_org["id"])
            name = await flows.workflow(
                _nodes(await flows.function("complete")), THREE_STEPS_EDGES
            )
            flows_by_pod[flows.pod_id] = UUID(
                str(
                    await flows.scalar(
                        "SELECT id FROM workflow_flows WHERE name = :name", name=name
                    )
                )
            )

        budget = DbBudget.from_settings(CLASSES)
        # Named, so the runtime's connections can be told apart from the
        # harness's own (the callback settler stands in for the API and uses
        # the test's pool), autovacuum and the probe itself.
        engine = create_async_engine(
            e2e_settings.database_url,
            pool_size=budget.total,
            max_overflow=0,
            connect_args={"server_settings": {"application_name": RUNTIME_APP}},
        )
        runtime = WorkRuntime(
            async_sessionmaker(engine, expire_on_commit=False),
            classes=CLASSES,
            registry=flows.registry,
            budget=budget,
        )
        peak_runtime = peak_database = 0

        async def completed() -> int:
            # Sampled on each probe: the peaks are what was held while the
            # burst ran, not a timing measurement.
            nonlocal peak_runtime, peak_database
            counts = await flows.scalar(
                "SELECT json_build_object('runtime', "
                "count(*) FILTER (WHERE application_name = :app), "
                "'database', count(*)) FROM pg_stat_activity "
                "WHERE datname = current_database()",
                app=RUNTIME_APP,
            )
            assert isinstance(counts, dict)
            peak_runtime = max(peak_runtime, int(counts["runtime"]))
            peak_database = max(peak_database, int(counts["database"]))
            done = await flows.scalar(
                "SELECT count(*) FROM workflow_flow_runs WHERE status = 'COMPLETED'"
            )
            return int(done)  # type: ignore[arg-type]

        await _enqueue_starts(flows, flows_by_pod)
        total = (RUNS // PODS) * PODS
        began = time.monotonic()
        await runtime.start()
        try:
            await eventually(
                label="every run completed",
                probe=completed,
                done=lambda n: n >= total,
                timeout_seconds=max(600, total / 5),
                interval_seconds=0.5,
                fail_fast=lambda _: None,
            )
            elapsed = time.monotonic() - began
        finally:
            await runtime.stop()
            await engine.dispose()

        steps = {
            wait_type: await _percentiles(
                flows,
                "SELECT extract(epoch FROM w.completed_at - "
                + ("w.scheduled_at" if wait_type == "TIME" else "w.created_at")
                + f") AS s FROM workflow_run_waits w WHERE w.wait_type = '{wait_type}'",
            )
            for wait_type in ("FUNCTION", "TIME", "DECISION")
        }
        runs = await _percentiles(
            flows,
            "SELECT extract(epoch FROM completed_at - created_at) AS s "
            "FROM workflow_flow_runs WHERE status = 'COMPLETED'",
        )
        failed = await flows.scalar(
            "SELECT count(*) FROM workflow_flow_runs WHERE status <> 'COMPLETED'"
        )
        dead = await flows.scalar(
            "SELECT count(*) FROM work_items WHERE state = 'dead'"
        )
        result = {
            "runs": total,
            "pods": PODS,
            "seconds": round(elapsed, 1),
            "runs_per_second": round(total / elapsed, 1),
            "steps_per_second": round(3 * total / elapsed, 1),
            "step_latency_seconds": steps,
            "run_latency_seconds": runs,
            "runtime_pool": budget.total,
            "peak_runtime_connections": peak_runtime,
            # Everything on the database: the runtime, the harness's pool (the
            # callback settler and these probes), autovacuum.
            "peak_db_connections": peak_database,
            "not_completed": failed,
            "dead_items": dead,
            "function_invocations": flows.runtime.invocations.total(),
        }
        print("WORKFLOW_LOAD_RESULT " + json.dumps(result))
        assert 0 < peak_runtime <= budget.total, result
        assert failed == 0
        assert dead == 0
        assert flows.runtime.invocations.total() == total
        assert flows.decisions.asked == total
