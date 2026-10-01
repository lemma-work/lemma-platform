"""A schedule's triage end to end: each route, the digest, the question and the ceiling.

No Typesafe key is configured in e2e and the model is a deterministic mock, so
every decision here is answered by the decider's rules, which match on the
event's `value`. That makes each route a choice of event: `urgent` acts,
`later` goes to the digest, `unsure` asks a person and `noise` is ignored.

The webhook path is driven through `handle_llm_filter_task` directly, as the
filter's e2e does; the DATASTORE path through a real record write and the
worker. Fires are claimed and started by the worker either way.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.handlers.schedule_consumer import handle_llm_filter_task
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.services.digest_dispatcher import dispatch_due_digests
from app.modules.schedule.services.run_recovery_service import (
    ScheduleRunRecoveryService,
)
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    SCHEDULE_E2E_TIMEOUT_SECONDS,
    _create_datastore_table,
    _create_pod,
    _create_workflow,
    _seed_composio_webhook_trigger,
    _workflow_run,
    _workflow_runs,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

DECIDER = "event-triage"
DEFINITION = {
    "description": "What happens to each event a schedule receives.",
    "questions": {
        "action": {
            "type": "choice",
            "prompt": "What should happen with this event?",
            "options": {
                "urgent": "Someone is waiting on it.",
                "later": "Worth knowing, not urgent.",
                "unsure": "Needs a person to say.",
                "noise": "Nothing anyone needs.",
            },
            "fallback": "unsure",
        }
    },
    "rules": [
        {"when": f"value == '{option}'", "answer": {"action": option}}
        for option in ("urgent", "later", "unsure", "noise")
    ],
}
ROUTES = {"urgent": "act", "later": "digest", "unsure": "ask", "noise": "ignore"}
HOURLY = {"cron": "0 * * * *"}


async def _define_decider(client: AsyncClient, pod_id: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/deciders", json={"name": DECIDER, "definition": DEFINITION}
    )
    assert response.status_code == 201, response.text


async def _create_triaged_schedule(
    client: AsyncClient,
    pod_id: str,
    *,
    schedule_type: str,
    workflow_name: str,
    config: dict,
    triage: dict,
    expected_status: int = 201,
) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": schedule_type,
            "workflow_name": workflow_name,
            "config": config,
            "triage": triage,
        },
    )
    assert response.status_code == expected_status, response.text
    return response.json()


async def _webhook_schedule(
    client: AsyncClient, db_session: AsyncSession, org_id: str, triage: dict
) -> tuple[str, dict, dict]:
    await _seed_composio_webhook_trigger(db_session)
    pod_id = await _create_pod(client, org_id)
    await _define_decider(client, pod_id)
    workflow = await _create_workflow(
        client,
        pod_id,
        start={
            "type": "EVENT",
            "config": {
                "connector_id": "composio",
                "connector_trigger_id": "OUTLOOK_MESSAGE_TRIGGER",
                "trigger_config": {"source": "composio"},
            },
        },
        name_prefix="triaged-webhook-workflow",
    )
    schedule = await _create_triaged_schedule(
        client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        config={"source": "composio", "provider_trigger_id": f"trg-{uuid4().hex[:8]}"},
        triage=triage,
    )
    return pod_id, workflow, schedule


async def _deliver(schedule: dict, value: str, *, n: int = 0) -> str:
    """One webhook event, as the filter task receives it; its source event id."""
    source_event_id = f"composio:{uuid4().hex}"
    await handle_llm_filter_task(
        {"value": value, "n": n, "subject": f"{value} #{n}"},
        {"source": "composio", "source_event_id": source_event_id},
        schedule_id=schedule["id"],
        source_event_id=source_event_id,
    )
    return source_event_id


async def _runs(
    client: AsyncClient, pod_id: str, schedule_id: str, *, status: str | None = None
) -> list[dict]:
    response = await client.get(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs",
        params={"status": status} if status else None,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _wait_for_runs(
    client: AsyncClient, pod_id: str, schedule_id: str, expected: dict[str, int]
) -> list[dict]:
    def matches(items: list[dict]) -> bool:
        counts = {
            status: sum(item["status"] == status for item in items)
            for status in expected
        }
        return counts == expected and len(items) == sum(expected.values())

    return await eventually(
        label=f"schedule runs {expected}",
        probe=lambda: _runs(client, pod_id, schedule_id),
        done=matches,
        timeout_seconds=SCHEDULE_E2E_TIMEOUT_SECONDS,
        interval_seconds=0.2,
    )


async def _completed_workflow_starts(
    client: AsyncClient, pod_id: str, workflow_name: str, *, count: int
) -> list[dict]:
    """The `start` context of each completed run of the workflow, oldest first."""

    async def probe() -> list[dict]:
        starts = []
        for summary in await _workflow_runs(client, pod_id, workflow_name):
            if summary["status"] == "COMPLETED":
                run = await _workflow_run(client, pod_id, summary["id"])
                starts.append(run["execution_context"]["start"])
        return starts

    return await eventually(
        label=f"{count} completed runs of {workflow_name}",
        probe=probe,
        done=lambda starts: len(starts) >= count,
        timeout_seconds=SCHEDULE_E2E_TIMEOUT_SECONDS,
        interval_seconds=0.2,
    )


async def test_a_datastore_triage_fires_what_acts_and_skips_what_it_ignores(
    authenticated_client: AsyncClient,
    fixed_test_org,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    await _define_decider(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="triaged-datastore-workflow",
    )
    schedule = await _create_triaged_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
        triage={"decider": DECIDER, "routes": ROUTES, "digest": HOURLY},
    )
    assert schedule["triage"]["question"] == "action"
    assert schedule["next_digest_at"] is not None

    for value in ("urgent", "noise"):
        record = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/tables/schedule_records/records",
            json={"data": {"source": f"{value}-record", "value": value}},
        )
        assert record.status_code == 201, record.text

    runs = await _wait_for_runs(
        authenticated_client, pod_id, schedule["id"], {"COMPLETED": 1, "FILTERED": 1}
    )
    by_status = {run["status"]: run for run in runs}
    skipped = by_status["FILTERED"]
    assert skipped["llm_output"]["route"] == "ignore"
    assert skipped["llm_output"]["answer"] == "noise"
    assert skipped["payload"] == {}
    acted = by_status["COMPLETED"]
    assert acted["llm_output"]["route"] == "act"
    assert acted["llm_output"]["answer"] == "urgent"

    [start] = await _completed_workflow_starts(
        authenticated_client, pod_id, workflow["name"], count=1
    )
    assert start["payload"]["value"] == "urgent"
    assert start["llm_output"]["route"] == "act"

    decision = await authenticated_client.get(
        f"/pods/{pod_id}/decisions/{acted['llm_output']['decision_id']}"
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["answers"]["action"]["by"] == "rules"
    # The row is on an RLS table, so judging it is its owner's alone.
    assert decision.json()["visibility"] == "PERSONAL"


async def test_held_events_go_out_together_as_one_digest_run(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
    db_manager,
    worker,
):
    _ = worker
    pod_id, workflow, schedule = await _webhook_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        {"decider": DECIDER, "routes": ROUTES, "digest": HOURLY},
    )
    held_ids = [await _deliver(schedule, "later", n=n) for n in range(3)]

    held = await _runs(authenticated_client, pod_id, schedule["id"], status="HELD")
    assert sorted(run["source_event_id"] for run in held) == sorted(held_ids)
    assert {run["held_for"] for run in held} == {"digest"}
    assert all(run["payload"]["value"] == "later" for run in held)
    detail = (
        await authenticated_client.get(f"/pods/{pod_id}/schedules/{schedule['id']}")
    ).json()
    assert detail["last_fire_status"] == "HELD"
    assert await _workflow_runs(authenticated_client, pod_id, workflow["name"]) == []

    # Nothing the recovery sweep could repair: held is not a lost dispatch.
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        swept = await ScheduleRunRecoveryService(uow).recover()
    assert (swept.redelivered, swept.reconciled, swept.dead_lettered) == (0, 0, 0)

    due = datetime.fromisoformat(detail["next_digest_at"]) + timedelta(seconds=1)
    assert await dispatch_due_digests(factory, now=due) == 1
    # The occurrence is claimed once: a second sweep at the same moment sends nothing.
    assert await dispatch_due_digests(factory, now=due) == 0

    [start] = await _completed_workflow_starts(
        authenticated_client, pod_id, workflow["name"], count=1
    )
    assert start["payload"]["held"] == 3
    assert [event["n"] for event in start["payload"]["events"]] == [0, 1, 2]
    assert start["metadata"]["digest"] is True
    assert start["metadata"]["more_waiting"] is False

    runs = await _wait_for_runs(
        authenticated_client, pod_id, schedule["id"], {"COMPLETED": 1, "DISPATCHED": 3}
    )
    [digest_run] = [run for run in runs if run["status"] == "COMPLETED"]
    assert digest_run["source_event_id"].startswith("digest:")
    sent = [run for run in runs if run["status"] == "DISPATCHED"]
    assert {run["digest_run_id"] for run in sent} == {digest_run["id"]}
    assert {run["held_for"] for run in sent} == {None}
    async with factory() as uow:
        stored = (
            await uow.session.scalars(
                select(ScheduleRun).where(
                    ScheduleRun.schedule_id == UUID(schedule["id"]),
                    ScheduleRun.digest_run_id.is_not(None),
                )
            )
        ).all()
    # Out of the recovery index for good: the digest run reports their outcome.
    assert {row.target_outcome for row in stored} == {"DISPATCHED"}


async def test_an_ask_is_a_question_whose_answer_routes_the_event(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
    worker,
):
    _ = worker
    pod_id, workflow, schedule = await _webhook_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        {"decider": DECIDER, "routes": ROUTES, "digest": HOURLY},
    )
    source_event_id = await _deliver(schedule, "unsure")

    [held] = await _runs(authenticated_client, pod_id, schedule["id"], status="HELD")
    assert held["held_for"] == "ask"
    assert held["source_event_id"] == source_event_id

    listed = await authenticated_client.get(f"/pods/{pod_id}/notifications")
    assert listed.status_code == 200, listed.text
    [question] = [
        item
        for item in listed.json()["items"]
        if item["origin_kind"] == "SCHEDULE" and item["origin_id"] == schedule["id"]
    ]
    assert question["awaiting_response"] is True
    assert question["responds_through_action"] is False
    assert question["action"]["type"] == "CHOICE"
    assert question["action"]["run_id"] == held["id"]
    # Only what settles the event is offered: answering `unsure` would ask again.
    assert [option["key"] for option in question["action"]["options"]] == [
        "urgent",
        "later",
        "noise",
    ]
    assert "What should happen with this event?" in question["body"]

    refused = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{question['id']}/respond",
        json={"summary": "maybe tomorrow"},
    )
    assert refused.status_code == 422, refused.text

    answered = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{question['id']}/respond",
        json={"summary": "This one is urgent", "data": {"answer": "urgent"}},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["response_data"]["answer"] == "urgent"
    again = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{question['id']}/respond",
        json={"summary": "noise", "data": {"answer": "noise"}},
    )
    assert again.status_code == 409, again.text

    [run] = await _wait_for_runs(
        authenticated_client, pod_id, schedule["id"], {"COMPLETED": 1}
    )
    assert run["id"] == held["id"]
    assert run["llm_output"]["answered_by"] == "person"
    [start] = await _completed_workflow_starts(
        authenticated_client, pod_id, workflow["name"], count=1
    )
    assert start["payload"]["value"] == "unsure"
    assert start["llm_output"]["route"] == "act"

    decision = await authenticated_client.get(
        f"/pods/{pod_id}/decisions/{held['llm_output']['decision_id']}"
    )
    assert decision.status_code == 200, decision.text
    answer = decision.json()["answers"]["action"]
    # The person's answer is the decision's now, and it teaches the decider.
    assert (answer["value"], answer["by"]) == ("urgent", "person")


async def test_past_the_act_ceiling_an_event_goes_to_the_digest(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
    worker,
):
    _ = worker
    pod_id, _workflow, schedule = await _webhook_schedule(
        authenticated_client,
        db_session,
        fixed_test_org["id"],
        {"decider": DECIDER, "routes": ROUTES, "digest": HOURLY, "act_per_hour": 1},
    )
    await _deliver(schedule, "urgent", n=1)
    await _wait_for_runs(authenticated_client, pod_id, schedule["id"], {"COMPLETED": 1})

    second = await _deliver(schedule, "urgent", n=2)

    [held] = await _runs(authenticated_client, pod_id, schedule["id"], status="HELD")
    assert held["source_event_id"] == second
    assert held["held_for"] == "digest"
    assert held["llm_output"]["answer"] == "urgent"
    assert held["llm_output"]["route"] == "digest"
    assert held["llm_output"]["routed_from"] == "act"


async def test_a_triage_must_route_every_option_of_a_real_question(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    await _seed_composio_webhook_trigger(db_session)
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _define_decider(authenticated_client, pod_id)
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="untriaged-workflow",
    )
    config = {"table_name": "schedule_records", "operations": ["INSERT"]}

    missing = await _create_triaged_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config=config,
        triage={"decider": DECIDER, "routes": {"urgent": "act"}},
        expected_status=422,
    )
    assert missing["code"] == "SCHEDULE_VALIDATION_ERROR"
    assert "missing" in missing["message"]

    unknown = await _create_triaged_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config=config,
        triage={"decider": "nobody", "routes": ROUTES, "digest": HOURLY},
        expected_status=422,
    )
    assert "No decider named 'nobody'" in unknown["message"]

    schedule = await _create_triaged_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config=config,
        triage={"decider": DECIDER, "routes": ROUTES, "digest": HOURLY},
    )
    filtered = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"filter_instruction": "Only urgent rows."},
    )
    assert filtered.status_code == 422, filtered.text

    cleared = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"triage": None, "filter_instruction": "Only urgent rows."},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["triage"] is None
    assert cleared.json()["filter_instruction"] == "Only urgent rows."
