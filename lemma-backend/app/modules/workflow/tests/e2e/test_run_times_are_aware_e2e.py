"""A finished workflow run reports its times with their zone.

The run's times were stored without one, so a run the engine read back -- every
run that waited on something -- carried naive times, and so did the terminal
event it published. The schedule ledger compared that ``completed_at`` with an
aware time and raised. This walks a run through a form, so the engine finishes
it from a row it read back, and reads the terminal event exactly as a consumer
does: from its committed JSON payload, through the event's own model.
"""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workflow.contracts.run_outcomes import load_run_outcomes
from app.modules.workflow.domain.events import WorkflowRunTerminalEvent
from app.modules.workflow.infrastructure.models import WorkflowRunModel

pytestmark = [pytest.mark.e2e]


async def _form_workflow(client: AsyncClient, org_id: str) -> str:
    pod = await client.post(
        "/pods",
        json={
            "name": f"Run times {uuid4().hex[:6]}",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]
    workflow = await client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": "intake",
            "start": {"type": "MANUAL"},
            "nodes": [
                {
                    "id": "ask",
                    "type": "FORM",
                    "label": "Ask",
                    "config": {
                        "input_schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        }
                    },
                },
                {"id": "end", "type": "END", "label": "Done"},
            ],
            "edges": [{"id": "e1", "source": "ask", "target": "end"}],
        },
    )
    assert workflow.status_code == 201, workflow.text
    return pod_id


async def test_a_run_finished_after_a_wait_publishes_aware_times(
    authenticated_client: AsyncClient, fixed_test_org, db_manager, committed_events
) -> None:
    client = authenticated_client
    pod_id = await _form_workflow(client, fixed_test_org["id"])
    started = await client.post(f"/pods/{pod_id}/workflows/intake/runs")
    assert started.status_code == 201, started.text
    run_id = UUID(started.json()["id"])

    answered = await client.post(
        f"/pods/{pod_id}/workflow-runs/{run_id}/form",
        json={"node_id": "ask", "inputs": {"name": "Priya"}},
    )
    assert answered.status_code == 200, answered.text

    async def terminal_payloads() -> list[dict[str, object]]:
        return [
            p
            for p in committed_events.of_type(WorkflowRunTerminalEvent.get_event_type())
            if p.get("run_id") == str(run_id)
        ]

    payloads = await eventually(
        label="the run's terminal event",
        probe=terminal_payloads,
        done=lambda found: len(found) == 1,
    )
    event = WorkflowRunTerminalEvent.model_validate(payloads[0])

    async with db_manager.session_factory() as session:
        row = await session.get(WorkflowRunModel, run_id)
        assert row is not None
        assert row.status == "COMPLETED"
        stored_started, stored_completed = row.started_at, row.completed_at

    assert event.completed_at.tzinfo is not None
    assert event.started_at is not None and event.started_at.tzinfo is not None
    assert event.completed_at == stored_completed
    assert event.started_at == stored_started
    assert event.started_at <= event.completed_at

    # The ledger's other way to learn a run finished: reconciling, not the event.
    async with db_manager.session_factory() as session:
        outcomes = await load_run_outcomes(SqlAlchemyUnitOfWork(session), [run_id])
    ended_at = outcomes[run_id].ended_at
    assert ended_at is not None and ended_at.tzinfo is not None
    assert ended_at == stored_completed
