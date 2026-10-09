"""A schedule's filter, asked as a decision, through the real task.

The e2e stand-in model answers every yes/no with no, so every event here is
skipped -- which is the half of PS-SCHED-012 this suite can prove without a
real model: a skip is recorded, with the answer that caused it, apart from
failures; a provider that does not answer is retried and then dead-lettered,
never sent past the filter; and a redelivered event is not judged twice.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from streaq import StreaqRetry

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.request_context import bind_job_context
from app.modules.decisions.config import decisions_settings
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.handlers.schedule_consumer import (
    FILTER_MAX_TRIES,
    handle_llm_filter_task,
)
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.schedule_run_repository import (
    FILTER_NOT_CONFIGURED,
)
from app.modules.schedule.services.run_outcome_service import ScheduleRunOutcomeService
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_agent,
    _create_datastore_table,
    _create_pod,
    _create_schedule,
    _seed_connector_trigger,
)
from app.modules.test_support.e2e.function_helpers import seed_user
from app.modules.usage.infrastructure.models import UsageRecord

pytestmark = pytest.mark.e2e

EVENT = {"from": "billing@vendor.example.com", "subject": "Invoice 4411", "total": 1200}


async def _webhook_schedule(client, org, db_session) -> tuple[str, dict]:
    pod_id = await _create_pod(client, org["id"])
    agent = await _create_agent(client, pod_id)
    connector_id = f"filtered_{uuid4().hex[:8]}"
    connector_trigger_id = f"{connector_id}:message_created"
    await _seed_connector_trigger(
        db_session,
        connector_id=connector_id,
        trigger_id=connector_trigger_id,
        event_type="message.created",
        payload_schema={"type": "object"},
    )
    schedule = await _create_schedule(
        client,
        pod_id,
        schedule_type="WEBHOOK",
        agent_name=agent["name"],
        connector_trigger_id=connector_trigger_id,
        config={"source": "composio"},
        filter_instruction="Only invoices over 1000",
        filter_output_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["invoice", "receipt"]},
                "reason": {"type": "string"},
            },
        },
    )
    return pod_id, schedule


async def _judge(schedule: dict, event_id: str) -> None:
    await handle_llm_filter_task.fn(
        payload=EVENT,
        metadata={"provider": "custom"},
        schedule_id=schedule["id"],
        source_event_id=event_id,
    )


async def test_a_skipped_event_is_one_run_carrying_the_answers(
    authenticated_client, fixed_test_org, db_session
):
    pod_id, schedule = await _webhook_schedule(
        authenticated_client, fixed_test_org, db_session
    )

    await _judge(schedule, "evt-skip-1")
    # Redelivered: already judged, so neither a second row nor a second bill.
    await _judge(schedule, "evt-skip-1")

    skipped = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs", params={"skipped": True}
    )
    fired = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs", params={"skipped": False}
    )
    by_status = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs",
        params={"status": "FILTERED"},
    )
    assert skipped.status_code == 200, skipped.text
    runs = skipped.json()["items"]
    assert len(runs) == 1, runs
    assert runs[0]["status"] == ScheduleRunStatus.FILTERED.value
    answers = runs[0]["llm_output"]
    assert answers["should_proceed"] is False
    assert answers["kind"] in {"invoice", "receipt", None}
    assert "reason" not in answers, "free text is not a closed question"
    assert answers["_decision"]["provider"] == "model"
    assert fired.json()["items"] == []
    assert [run["id"] for run in by_status.json()["items"]] == [runs[0]["id"]]

    stored = await db_session.get(Schedule, UUID(schedule["id"]))
    await db_session.refresh(stored)
    assert stored.last_fire_status == "FILTERED"
    metered = await db_session.scalar(
        select(func.count())
        .select_from(UsageRecord)
        .where(
            UsageRecord.pod_id == UUID(pod_id),
            UsageRecord.source_type == "schedule_filter",
        )
    )
    assert metered == 1, "the redelivered event was judged and billed again"


async def test_an_unanswered_filter_is_retried_then_dead_lettered_not_sent_on(
    authenticated_client, fixed_test_org, db_session, monkeypatch
):
    pod_id, schedule = await _webhook_schedule(
        authenticated_client, fixed_test_org, db_session
    )
    monkeypatch.setattr(decisions_settings, "decision_provider", "typesafe")
    monkeypatch.setattr(decisions_settings, "typesafe_api_key", SecretStr("sk-e2e"))
    monkeypatch.setattr(decisions_settings, "typesafe_base_url", "http://127.0.0.1:9")

    with pytest.raises(StreaqRetry):
        await _judge(schedule, "evt-outage-1")
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ScheduleRun)
            .where(ScheduleRun.schedule_id == UUID(schedule["id"]))
        )
        == 0
    ), "a retryable failure must not leave a run behind"

    with bind_job_context(
        job_id="schedule-filter-e2e",
        task_name="handle_llm_filter_task",
        attempt=FILTER_MAX_TRIES,
    ):
        await _judge(schedule, "evt-outage-1")

    runs = (
        await authenticated_client.get(
            f"/pods/{pod_id}/schedules/{schedule['id']}/runs"
        )
    ).json()["items"]
    assert len(runs) == 1, runs
    assert runs[0]["status"] == ScheduleRunStatus.DEAD_LETTERED.value
    assert runs[0]["error_type"] == "ScheduleFilterUnavailable"
    # Kept, so a person retrying it by hand starts the target with the event.
    assert runs[0]["payload"] == EVENT
    stored = await db_session.get(Schedule, UUID(schedule["id"]))
    await db_session.refresh(stored)
    assert stored.is_active is True
    assert stored.consecutive_failures == 0, "an outage is not the schedule's failure"


async def test_a_filter_keeps_working_on_the_model_without_a_typesafe_key(
    authenticated_client, fixed_test_org, db_session, monkeypatch
):
    """Typesafe chosen but its key never set: the deployment's own model
    answers, rather than every filtered schedule dead-lettering."""
    pod_id, schedule = await _webhook_schedule(
        authenticated_client, fixed_test_org, db_session
    )
    monkeypatch.setattr(decisions_settings, "decision_provider", "typesafe")
    monkeypatch.setattr(decisions_settings, "typesafe_api_key", None)

    await _judge(schedule, "evt-no-typesafe-1")

    runs = (
        await authenticated_client.get(
            f"/pods/{pod_id}/schedules/{schedule['id']}/runs",
            params={"skipped": True},
        )
    ).json()["items"]
    assert [run["status"] for run in runs] == [ScheduleRunStatus.FILTERED.value]
    assert runs[0]["llm_output"]["_decision"]["provider"] == "model"


async def test_a_deployment_without_a_provider_is_not_counted_against_a_schedule(
    authenticated_client, fixed_test_org, db_session
):
    """No decision provider at all is the operator's to fix, not any owner's:
    counting it would switch off every filtered schedule at once. A filter that
    is the schedule's own problem is still counted."""
    _pod_id, created = await _webhook_schedule(
        authenticated_client, fixed_test_org, db_session
    )
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    for index in range(schedule_settings.schedule_max_consecutive_failures + 1):
        async with uow_factory() as uow:
            schedule = await ScheduleRepository(uow=uow).get(UUID(created["id"]))
            await ScheduleRunOutcomeService(uow).record_pre_dispatch_failure(
                schedule,
                source_event_id=f"evt-unconfigured-{index}",
                error_type=FILTER_NOT_CONFIGURED,
            )

    stored = await db_session.get(Schedule, UUID(created["id"]))
    await db_session.refresh(stored)
    assert stored.is_active is True
    assert stored.consecutive_failures == 0

    async with uow_factory() as uow:
        schedule = await ScheduleRepository(uow=uow).get(UUID(created["id"]))
        await ScheduleRunOutcomeService(uow).record_pre_dispatch_failure(
            schedule,
            source_event_id="evt-invalid-1",
            error_type="ScheduleFilterInvalid",
        )
    await db_session.refresh(stored)
    assert stored.consecutive_failures == 1


async def test_a_table_change_is_judged_as_the_owner_of_the_row(
    authenticated_client, fixed_test_org, db_session
):
    """Table changes are judged by this same task now, not inline on the
    stream, and run as the changed row's owner (PS-SCHED-011)."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    table = f"signups_{uuid4().hex[:8]}"
    await _create_datastore_table(authenticated_client, pod_id, table)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="DATASTORE",
        agent_name=agent["name"],
        config={"table_name": table, "operations": ["INSERT"]},
        filter_instruction="Only signups from large companies",
    )
    row_owner = await seed_user(db_session)

    await handle_llm_filter_task.fn(
        payload={"id": "r1", "source": "web"},
        metadata={"table_name": table, "record_id": "r1", "operation": "INSERT"},
        schedule_id=schedule["id"],
        source_event_id="evt-row-1",
        user_id=str(row_owner.id),
    )

    run = await db_session.scalar(
        select(ScheduleRun).where(ScheduleRun.schedule_id == UUID(schedule["id"]))
    )
    assert run is not None
    assert run.status == ScheduleRunStatus.FILTERED.value
    assert run.user_id == row_owner.id
    assert run.llm_output["should_proceed"] is False


async def test_a_time_schedule_refuses_a_new_filter_but_keeps_an_old_one_editable(
    authenticated_client, fixed_test_org, db_session
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 9 * * *"},
        filter_instruction="Only weekdays",
        expected_status=422,
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 9 * * *"},
    )
    # Saved before the refusal existed.
    legacy = await db_session.get(Schedule, UUID(schedule["id"]))
    legacy.filter_instruction = "Only weekdays"
    await db_session.commit()
    path = f"/pods/{pod_id}/schedules/{schedule['id']}"

    resent = await authenticated_client.patch(
        path,
        json={"config": {"cron": "0 10 * * *"}, "filter_instruction": "Only weekdays"},
    )
    changed = await authenticated_client.patch(
        path, json={"filter_instruction": "Only weekends"}
    )
    cleared = await authenticated_client.patch(path, json={"filter_instruction": ""})

    assert resent.status_code == 200, resent.text
    assert changed.status_code == 422, changed.text
    assert cleared.status_code == 200, cleared.text
