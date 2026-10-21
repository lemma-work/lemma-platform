"""A schedule's AGENT target reports its outcome through the agent's run fact.

A reserved schedule run starts its agent conversation (``schedule.start_target``),
which writes the agent's turn in the same transaction; the turn's finalize
emits ``AgentRunFinished``, and ``schedule.agent_finished`` records the outcome
on the run in the same item. Replaces the ``schedule-agent-outcomes`` consumer
group. A run that ends on a pause is not the conversation finishing and records
nothing (``tests/unit/test_agent_outcome_subscription.py``).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import (
    WorkClass,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
)
from app.modules.agent.tests.e2e.turn_work_support import (
    drain,
    runtime_profile,
    scalar,
    turn_registry,
)
from app.modules.schedule.repositories.fire_ledger import FireLedger
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_pod,
    _create_schedule,
)
from app.modules.schedule.work.agent_outcome import register_agent_outcome_work
from app.modules.schedule.work.kinds import SCHEDULE_START, StartTarget
from app.modules.schedule.work.registration import register_schedule_work
from app.modules.test_support.e2e.scripted_model import (
    script_model_error,
)

pytestmark = [pytest.mark.e2e]

CLASSES = [WorkClass.INTERACTIVE, WorkClass.AUTOMATION, WorkClass.EVENTS]


def _registry():
    def schedule_kinds(registry) -> None:
        register_schedule_work(registry)
        register_agent_outcome_work(registry)

    return turn_registry(extra=(schedule_kinds,))


async def _scheduled_run(client, org_id: str, *, script: list | None) -> tuple:
    pod_id = await _create_pod(client, org_id)
    profile = await runtime_profile(client, org_id)
    agent_name = f"scheduled_{uuid4().hex[:6]}"
    agent = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Do the scheduled task.",
            "agent_runtime": {"profile_id": profile, "model_name": "mock-safe-model"},
            "toolsets": ["USER_INTERACTION"],
        },
    )
    assert agent.status_code == 201, agent.text
    schedule = await _create_schedule(
        client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent_name,
        config={"cron": "0 0 * * *"},
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        reserved = await FireLedger(uow.session).reserve(
            schedule_id=UUID(schedule["id"]),
            user_id=UUID(schedule["user_id"]),
            source_event_id=f"fire-{uuid4().hex[:8]}",
            target_kind="AGENT",
            payload={},
            metadata={},
            llm_output=None,
            source_occurred_at=None,
        )
        assert reserved is not None
        run = await FireLedger(uow.session).lock(reserved.run_id)
        assert run is not None
        await WorkWriter.of(uow).add(
            SCHEDULE_START,
            pod_namespace(UUID(pod_id)),
            StartTarget(schedule_run_id=run.id),
            rate_key=f"schedule:{schedule['id']}",
        )
    if script is not None:
        # The conversation is reserved under the run's target id: script it
        # before the start creates it.
        conversation_id = UUID(run.target_run_id)
        await _script_on_create(conversation_id, script)
    return run.id, UUID(run.target_run_id)


_SCRIPTS: dict[UUID, list] = {}


async def _script_on_create(conversation_id: UUID, script: list) -> None:
    _SCRIPTS[conversation_id] = script


async def _drain_with_scripts() -> None:
    """Start the targets, script their conversations, then run the turns."""
    registry = _registry()
    starts = WorkRegistry()
    register_schedule_work(starts)
    await drain(starts, classes=[WorkClass.AUTOMATION])  # starts only: turns wait
    from sqlalchemy import text

    async with async_session_maker() as session:
        for conversation_id, script in _SCRIPTS.items():
            await session.execute(
                text(
                    "UPDATE agent_conversations SET conversation_metadata = "
                    "coalesce(conversation_metadata, '{}'::jsonb) || "
                    "jsonb_build_object('mock_llm_script', CAST(:s AS jsonb)) "
                    "WHERE id = :c"
                ),
                {"s": __import__("json").dumps(script), "c": conversation_id},
            )
        await session.commit()
    _SCRIPTS.clear()
    await drain(registry, classes=CLASSES)


async def _status(run_id: UUID) -> str | None:
    """The outcome the target reported (the run's status follows from it)."""
    return await scalar(
        "SELECT target_outcome FROM schedule_runs WHERE id = :r", r=run_id
    )


@pytest.mark.drives_work
async def test_each_agent_outcome_lands_on_its_schedule_run(
    authenticated_client, fixed_test_org
) -> None:
    org = fixed_test_org["id"]
    completed, _ = await _scheduled_run(authenticated_client, org, script=None)
    failed, _ = await _scheduled_run(
        authenticated_client,
        org,
        script=[script_model_error("http", message="provider down", status_code=400)],
    )
    await _drain_with_scripts()

    assert await _status(completed) == "COMPLETED"
    assert await _status(failed) == "TARGET_FAILED"
    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'schedule.agent_finished' "
            "AND state <> 'queued'"
        )
        == 0
    )
