"""Schedules on the work runtime, one transition at a time, against real Postgres.

The API runs in-process; the work runtime is driven by ``run_until_idle`` so
each step is observable: what an API write armed, what a fire committed, what
a start did. No worker subprocess.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass, facts
from app.modules.schedule.contracts.webhook_source import NormalizedWebhook
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.schedule_run_repository import (
    DEPLOYMENT_FAILURES,
)
from app.modules.schedule.services.trigger_filter import (
    MAX_FILTER_ASKS,
    install_schedule_filter,
)
from app.modules.schedule.services.webhook_handler import WebhookHandler
from app.modules.schedule.services.webhook_schedule_matcher import (
    WebhookScheduleMatcher,
)
from app.modules.schedule.tests.e2e.trigger_work_support import (
    daily_fire_cap,
    EVERY_HALF_HOUR,
    CountingFilter,
    create_pod,
    create_time_schedule,
    execute,
    fire_due,
    items,
    run,
    runs,
    scalar,
    schedule_row,
)
from app.modules.workflow.contracts.run_work import WorkflowRunFinished

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


@pytest.fixture
def counting_filter():
    judge = CountingFilter()
    install_schedule_filter(judge)
    try:
        yield judge
    finally:
        install_schedule_filter(None)


async def test_creating_a_time_schedule_arms_exactly_one_fire_at_its_cursor(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)

    row = await schedule_row(schedule["id"])
    [fire] = await items("schedule.fire")
    assert fire.state == "queued"
    assert fire.ns == f"pod:{pod_id}"
    assert fire.run_at == row["next_fire_at"]
    assert fire.payload["schedule_id"] == schedule["id"]


async def test_a_fire_reserves_its_run_advances_and_starts_the_target_once(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)
    first_cursor = (await schedule_row(schedule["id"]))["next_fire_at"]

    await fire_due()

    [reserved] = await runs(schedule["id"])
    assert reserved["source_event_id"] == (
        f"cron:{schedule['id']}:{first_cursor.astimezone(timezone.utc).isoformat()}"
    )
    assert (reserved["status"], reserved["attempts"]) == ("PROCESSING", 0)
    advanced = (await schedule_row(schedule["id"]))["next_fire_at"]
    assert advanced > first_cursor
    [next_fire] = await items("schedule.fire", state="queued")
    assert next_fire.run_at == advanced
    [start] = await items("schedule.start_target")
    assert start.rate_key == f"schedule:{schedule['id']}"

    await run(WorkClass.AUTOMATION)

    [started] = await runs(schedule["id"])
    assert (started["status"], started["attempts"]) == ("DISPATCHED", 1)
    conversation = await scalar(
        "SELECT count(*) FROM agent_conversations WHERE id = CAST(:id AS uuid)",
        id=started["target_run_id"],
    )
    assert conversation == 1
    # The last fire is read from the run, not stamped on the schedule.
    detail = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}"
    )
    assert detail.json()["last_fire_status"] == "TRIGGERED"
    assert detail.json()["last_run_id"] == started["target_run_id"]


async def test_editing_pausing_or_deleting_turns_the_pending_fire_into_a_no_op(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    edited = await create_time_schedule(authenticated_client, pod_id)
    paused = await create_time_schedule(authenticated_client, pod_id)
    deleted = await create_time_schedule(authenticated_client, pod_id)

    response = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{edited['id']}",
        json={"config": {"cron": EVERY_HALF_HOUR}},
    )
    assert response.status_code == 200, response.text
    response = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{paused['id']}", json={"is_active": False}
    )
    assert response.status_code == 200, response.text
    response = await authenticated_client.delete(
        f"/pods/{pod_id}/schedules/{deleted['id']}"
    )
    assert response.status_code == 204, response.text

    await fire_due()

    # The edit's own cursor fired once; the superseded item did nothing.
    assert len(await runs(edited["id"])) == 1
    assert await runs(paused["id"]) == []
    viable = [
        item
        for item in await items("schedule.fire", state="queued")
        if item.payload["schedule_id"] == edited["id"]
    ]
    assert len(viable) == 1
    assert [
        item
        for item in await items("schedule.fire")
        if item.payload["schedule_id"] in (paused["id"], deleted["id"])
    ] == []

    resumed = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{paused['id']}", json={"is_active": True}
    )
    assert resumed.status_code == 200, resumed.text
    [rearmed] = [
        item
        for item in await items("schedule.fire")
        if item.payload["schedule_id"] == paused["id"]
    ]
    assert rearmed.run_at == (await schedule_row(paused["id"]))["next_fire_at"]


async def test_a_one_shot_fires_once_and_retires_without_a_next_fire(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    at = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)
    schedule = await create_time_schedule(
        authenticated_client, pod_id, cron=None, scheduled_at=at
    )

    await fire_due()
    await fire_due()

    assert len(await runs(schedule["id"])) == 1
    row = await schedule_row(schedule["id"])
    assert (row["is_active"], row["next_fire_at"], row["paused_reason"]) == (
        False,
        None,
        None,
    )
    assert await items("schedule.fire") == []
    # Its run still starts, though the schedule is no longer active.
    await run(WorkClass.AUTOMATION)
    assert (await runs(schedule["id"]))[0]["status"] == "DISPATCHED"


async def _webhook_schedule(
    pod_id: UUID, owner_id: UUID, *, filter_instruction: str | None = None
) -> ScheduleEntity:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        entity = await ScheduleRepository(uow=uow).create(
            ScheduleEntity(
                id=uuid4(),
                user_id=owner_id,
                pod_id=pod_id,
                agent_id=pod_id,
                schedule_type=ScheduleType.WEBHOOK,
                config={"source": "custom", "event": f"ping-{uuid4().hex[:6]}"},
                instruction="Look at the event.",
                filter_instruction=filter_instruction,
            )
        )
        await uow.commit()
    return entity


def _webhook_handler() -> WebhookHandler:
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

    return WebhookHandler(
        matcher_factory=lambda uow: WebhookScheduleMatcher(
            schedule_repository=ScheduleRepository(uow=uow)
        ),
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
    )


async def _deliver(schedule: ScheduleEntity, event_id: str) -> list[UUID]:
    return await _webhook_handler().handle_webhook(
        source="custom",
        payload={},
        normalized=NormalizedWebhook(
            payload={"hello": "world"},
            source_event_id=event_id,
            match=dict(schedule.config),
        ),
    )


async def test_a_webhook_fires_in_its_request_and_a_provider_retry_does_not(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(pod_id, UUID(fixed_test_user["id"]))

    assert await _deliver(schedule, "provider-evt-1") == [schedule.id]
    assert await _deliver(schedule, "provider-evt-1") == [schedule.id]

    [reserved] = await runs(schedule.id)
    assert reserved["source_event_id"] == "provider-evt-1"
    assert len(await items("schedule.start_target")) == 1
    await run(WorkClass.AUTOMATION)
    assert (await runs(schedule.id))[0]["status"] == "DISPATCHED"


async def test_a_filtered_webhook_asks_the_model_once_however_often_it_arrives(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user, counting_filter
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(
        pod_id, UUID(fixed_test_user["id"]), filter_instruction="Only real pings"
    )

    await _deliver(schedule, "evt-filtered")
    [filter_item] = await items("schedule.filter")
    assert filter_item.rate_key == f"schedule:{schedule.id}"
    await run(WorkClass.AUTOMATION, WorkClass.EVENTS)
    # Redelivered after the verdict: found, not asked again.
    await _deliver(schedule, "evt-filtered")
    await run(WorkClass.AUTOMATION, WorkClass.EVENTS)

    assert counting_filter.asked == ["evt-filtered"]
    [fired] = await runs(schedule.id)
    assert fired["status"] == "DISPATCHED"
    assert fired["llm_output"]["should_proceed"] is True

    counting_filter.proceed = False
    await _deliver(schedule, "evt-skipped")
    await run(WorkClass.AUTOMATION)
    skipped = (await runs(schedule.id))[-1]
    assert (skipped["source_event_id"], skipped["status"]) == (
        "evt-skipped",
        "FILTERED",
    )
    assert counting_filter.asked == ["evt-filtered", "evt-skipped"]


async def _interrupted_claim(schedule, user_id: str, event: str, asks: int) -> None:
    """What an item interrupted mid-question leaves: its claim, and its item."""
    [item] = await items("schedule.filter")
    await execute(
        "INSERT INTO schedule_runs (id, schedule_id, user_id, source_event_id, status, "
        "attempts, target_kind, target_run_id, payload, metadata, llm_output, "
        "created_at, updated_at) VALUES (gen_random_uuid(), :sid, :uid, "
        ":evt, 'FILTERING', 0, 'AGENT', gen_random_uuid()::text, '{}', "
        "jsonb_build_object('filter_item_id', CAST(:item AS text), "
        "'filter_asks', CAST(:asks AS int)), '{}', now(), now())",
        sid=str(schedule.id),
        uid=user_id,
        evt=event,
        item=str(item.id),
        asks=asks,
    )


async def test_a_filter_interrupted_once_is_asked_again_and_fires(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user, counting_filter
):
    """The claim is committed before the call; a replay of the same item finds
    its own claim. The question has no effect until its verdict commits, so it
    is asked again -- once -- rather than losing the event."""
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(
        pod_id, UUID(fixed_test_user["id"]), filter_instruction="Only real pings"
    )
    await _deliver(schedule, "evt-interrupted")
    await _interrupted_claim(schedule, fixed_test_user["id"], "evt-interrupted", 1)

    await run(WorkClass.AUTOMATION)

    assert counting_filter.asked == ["evt-interrupted"]
    [run_row] = await runs(schedule.id)
    assert run_row["status"] == "DISPATCHED"
    assert run_row["metadata"]["filter_asks"] == 2


async def test_a_filter_interrupted_every_time_is_dead_lettered_uncounted(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user, counting_filter
):
    """Asked as often as allowed and interrupted each time: no third bill. The
    run says why, and the breaker does not count the deployment's interruption."""
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(
        pod_id, UUID(fixed_test_user["id"]), filter_instruction="Only real pings"
    )
    await _deliver(schedule, "evt-interrupted")
    await _interrupted_claim(
        schedule, fixed_test_user["id"], "evt-interrupted", MAX_FILTER_ASKS
    )

    await run(WorkClass.AUTOMATION)

    assert counting_filter.asked == []
    [run_row] = await runs(schedule.id)
    assert (run_row["status"], run_row["error_type"]) == (
        "DEAD_LETTERED",
        "ScheduleFilterInterrupted",
    )
    assert "ScheduleFilterInterrupted" in DEPLOYMENT_FAILURES


async def test_the_daily_ceiling_pauses_the_trigger_records_the_skip_and_tells_the_owner(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(pod_id, UUID(fixed_test_user["id"]))
    # A per-pod override of the ceiling, stated by the limit port.
    async with daily_fire_cap(pod_id, 3):
        for n in range(4):
            await _deliver(schedule, f"evt-{n}")
        assert await _deliver(schedule, "evt-after-pause") == []

    fired = [r for r in await runs(schedule.id) if r["status"] == "PROCESSING"]
    assert [r["source_event_id"] for r in fired] == ["evt-0", "evt-1", "evt-2"]
    row = await schedule_row(schedule.id)
    assert (row["is_active"], row["paused_reason"]) == (False, "fire_cap")
    skips = await scalar(
        "SELECT count(*) FROM trigger_skips WHERE schedule_id = :id "
        "AND reason = 'fire_cap' AND first = 'evt-3'",
        id=str(schedule.id),
    )
    assert skips == 1
    [notice] = await items("schedule.paused_notice")
    assert notice.payload["reason"] == "fire_cap"
    detail = await authenticated_client.get(f"/pods/{pod_id}/schedules/{schedule.id}")
    assert detail.json()["paused_reason"] == "fire_cap"

    resumed = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule.id}", json={"is_active": True}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["paused_reason"] is None


async def test_a_workflow_finishing_records_its_schedule_runs_outcome(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(pod_id, UUID(fixed_test_user["id"]))
    target_run_id = uuid4()
    await execute(
        "INSERT INTO schedule_runs (id, schedule_id, user_id, source_event_id, status, "
        "attempts, target_kind, target_run_id, payload, metadata, llm_output, "
        "created_at, updated_at) VALUES (gen_random_uuid(), :sid, :uid, 'evt-wf', "
        "'DISPATCHED', 1, 'WORKFLOW', :target, '{}', '{}', '{}', now(), now())",
        sid=str(schedule.id),
        uid=fixed_test_user["id"],
        target=str(target_run_id),
    )
    async with async_session_maker() as session:
        written = await facts.emit(
            SqlAlchemyUnitOfWork(session),
            WorkflowRunFinished(
                run_id=target_run_id,
                pod_id=pod_id,
                flow_id=uuid4(),
                status="FAILED",
                schedule_event_id=f"{schedule.id}:evt-wf",
            ),
        )
        await session.commit()
    assert written == 1

    await run(WorkClass.EVENTS)

    [finished] = await runs(schedule.id)
    assert (finished["target_outcome"], finished["error_type"]) == (
        "TARGET_FAILED",
        "WorkflowRunFailed",
    )


async def test_redriving_a_failed_run_starts_it_through_the_runtime(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(pod_id, UUID(fixed_test_user["id"]))
    failed_id = uuid4()
    await execute(
        "INSERT INTO schedule_runs (id, schedule_id, user_id, source_event_id, status, "
        "attempts, target_kind, target_run_id, payload, metadata, llm_output, "
        "created_at, updated_at) VALUES (:id, :sid, :uid, 'evt-failed', "
        "'DEAD_LETTERED', 1, 'AGENT', gen_random_uuid()::text, '{}', '{}', '{}', "
        "now(), now())",
        id=str(failed_id),
        sid=str(schedule.id),
        uid=fixed_test_user["id"],
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/schedules/{schedule.id}/runs/{failed_id}/retry"
    )
    assert response.status_code == 202, response.text
    await run(WorkClass.AUTOMATION)

    redrive = [r for r in await runs(schedule.id) if r["id"] != failed_id]
    assert [r["status"] for r in redrive] == ["DISPATCHED"]


async def test_a_schedule_down_for_hours_fires_once_and_resumes_in_the_future(
    authenticated_client: AsyncClient, fixed_test_org
):
    """A backlog collapses into one late fire; it is not replayed, nor dropped."""
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)
    [fire] = await items("schedule.fire")
    behind = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(
        hours=3
    )
    # As if the item had been due three hours ago and nothing ran it.
    await execute(
        "UPDATE schedules SET next_fire_at = :at WHERE id = :id",
        at=behind,
        id=schedule["id"],
    )
    await execute(
        "UPDATE work_items SET payload = jsonb_set(payload, '{fire_at}', to_jsonb(CAST(:at AS text))) "
        "WHERE id = :id",
        at=behind.isoformat(),
        id=str(fire.id),
    )

    await fire_due()

    assert len(await runs(schedule["id"])) == 1
    upcoming = (await schedule_row(schedule["id"]))["next_fire_at"]
    assert upcoming > datetime.now(timezone.utc)


async def test_an_unusable_time_config_is_retired_by_its_arm_saying_why(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule_id = str(uuid4())
    await execute(
        "INSERT INTO schedules (id, user_id, pod_id, schedule_type, agent_id, config, "
        "instruction, visibility, is_active, is_internal, include_outside_rows, "
        "consecutive_failures, created_at, updated_at) VALUES (:id, :uid, :pod, 'TIME', "
        ":pod, '{\"cron\": \"not a cron\"}', 'hi', 'POD', true, false, false, 0, now(), now())",
        id=schedule_id,
        uid=fixed_test_user["id"],
        pod=str(pod_id),
    )
    async with async_session_maker() as session:
        from app.modules.schedule.repositories.time_arming import request_arm

        await request_arm(session, schedule_id=UUID(schedule_id), pod_id=pod_id)
        await session.commit()

    await run(WorkClass.EVENTS)

    row = await schedule_row(schedule_id)
    assert (row["is_active"], row["paused_reason"]) == (
        False,
        "SCHEDULE_VALIDATION_ERROR",
    )
    [notice] = await items("schedule.paused_notice")
    assert notice.payload["reason"] == "SCHEDULE_VALIDATION_ERROR"
    assert await items("schedule.fire") == []


async def test_a_fire_out_of_tries_pauses_its_schedule_and_tells_the_owner(
    authenticated_client: AsyncClient, fixed_test_org, monkeypatch
):
    """A dead fire item is the schedule's only chain: without it nothing runs
    the schedule again. It is paused, saying why, instead of going quiet."""
    from app.core.infrastructure.work import WorkRegistry
    from app.core.infrastructure.work.testing import make_due, run_until_idle
    from app.modules.schedule.tests.e2e.broken_fire import broken_time_fire
    from app.modules.schedule.work import fire
    from app.modules.schedule.work.registration import register_schedule_work

    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)

    monkeypatch.setattr(fire, "run_time_fire", broken_time_fire)
    registry = WorkRegistry()
    register_schedule_work(registry)
    for _ in range(3):
        await make_due(async_session_maker, kind="schedule.fire")
        await run_until_idle(
            async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
        )

    [dead] = await items("schedule.fire", state="dead")
    assert dead.payload["schedule_id"] == schedule["id"]
    row = await schedule_row(schedule["id"])
    assert (row["is_active"], row["paused_reason"]) == (False, "fire_failed")
    [notice] = await items("schedule.paused_notice")
    assert notice.payload["reason"] == "fire_failed"

    resumed = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}", json={"is_active": True}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["paused_reason"] is None
    assert len(await items("schedule.fire", state="queued")) == 1


async def test_the_arm_audit_rearms_a_time_schedule_that_lost_its_fire_item(
    authenticated_client: AsyncClient, fixed_test_org, monkeypatch
):
    from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkWriter
    from app.modules.schedule.work import audit
    from app.modules.schedule.work.kinds import SCHEDULE_ARM_AUDIT, ArmAudit

    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    kept = await create_time_schedule(authenticated_client, pod_id)
    lost = await create_time_schedule(authenticated_client, pod_id)
    await execute(
        "DELETE FROM work_items WHERE kind = 'schedule.fire' "
        "AND payload->>'schedule_id' = :id",
        id=lost["id"],
    )
    counted: list[int] = []
    monkeypatch.setattr(
        audit, "rearmed", SimpleNamespace(add=lambda n, *a: counted.append(n))
    )

    async with async_session_maker() as session:
        await WorkWriter(session).add(SCHEDULE_ARM_AUDIT, SYSTEM_NAMESPACE, ArmAudit())
        await session.commit()
    await run(WorkClass.ADMIN)
    await run(WorkClass.EVENTS)

    assert sum(counted) >= 1
    fires = {
        item.payload["schedule_id"]: item
        for item in await items("schedule.fire", state="queued")
    }
    assert fires[lost["id"]].run_at == (await schedule_row(lost["id"]))["next_fire_at"]
    assert kept["id"] in fires
    # Audited again with nothing missing: nothing more is armed.
    counted.clear()
    async with async_session_maker() as session:
        await WorkWriter(session).add(SCHEDULE_ARM_AUDIT, SYSTEM_NAMESPACE, ArmAudit())
        await session.commit()
    await run(WorkClass.ADMIN)
    assert not any(
        item.payload["schedule_id"] in (kept["id"], lost["id"])
        for item in await items("schedule.arm")
    )
