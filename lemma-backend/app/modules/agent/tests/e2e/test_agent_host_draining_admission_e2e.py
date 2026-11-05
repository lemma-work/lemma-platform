"""A computer that is shutting down takes no new run, and keeps the one it has.

A host shut down cleanly reports DRAINING and stops taking work. Admission
still accepted a new run for it, which then waited out the whole host-wait
timeout and failed with a sentence naming neither the computer nor a fix.
The run it already had is another matter: re-admitting it must still find its
own dispatch, not a refusal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.agent_host.control import admission
from app.modules.agent.agent_host.domain.host import AgentHostRunSpec, AgentHostStatus
from app.modules.agent.agent_host.control.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.agent_host.control.repository_common import (
    AgentHostRunConflict,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostModel,
    AgentRuntimeProfileModel,
)
from app.modules.agent.tests.e2e.agent_host_helpers import paired_machine

pytestmark = [pytest.mark.e2e]


async def _run(db_session, scenario) -> tuple[UUID, UUID]:
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "draining"}
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])
    run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    return run.id, conversation_id


def _spec(run_id: UUID, conversation_id: UUID, harness_id: UUID) -> AgentHostRunSpec:
    return AgentHostRunSpec(
        agent_run_id=run_id,
        conversation_id=conversation_id,
        harness_id=harness_id,
        profile_revision="rev-1",
        model_name="gpt-5-codex",
        config_selections={},
        system_prompt="Draining test.",
        prompt=[{"role": "user", "content": "Hello."}],
        run_deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
    )


async def test_a_draining_host_refuses_a_new_run_and_keeps_its_own(
    db_session, scenario
) -> None:
    await scenario.create_org_with_pod(name_prefix="Draining")
    machine = await paired_machine(scenario, display_name="Ada's MacBook")
    profile = AgentRuntimeProfileModel(
        organization_id=UUID(scenario.org_id),
        runtime_type="HARNESS",
        harness_id=machine["harness_id"],
        user_id=UUID(scenario.owner_user["id"]),
        scope="PERSONAL",
        kind="HARNESS",
        protocol="AGENT_HOST",
        name=f"Draining profile {uuid4().hex[:8]}",
        default_model_name="gpt-5-codex",
        model_catalog=[],
        config={"harness_id": str(machine["harness_id"]), "config_options": []},
    )
    db_session.add(profile)
    await db_session.commit()
    repository = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))

    async def admit(spec: AgentHostRunSpec):
        return await admission.enqueue_run(
            repository.uow,
            host_id=machine["host_id"],
            harness_id=machine["harness_id"],
            runtime_profile_id=profile.id,
            run_spec=spec,
            encrypted_mcp_payload={"encrypted": True},
        )

    running_id, running_conversation = await _run(db_session, scenario)
    running = _spec(running_id, running_conversation, machine["harness_id"])
    admitted = await admit(running)
    await db_session.commit()

    await db_session.execute(
        update(AgentHostModel)
        .where(AgentHostModel.id == machine["host_id"])
        .values(status=AgentHostStatus.DRAINING.value)
    )
    await db_session.commit()

    # The run it already has is still its own.
    assert (await admit(running)).id == admitted.id
    await db_session.commit()

    new_id, new_conversation = await _run(db_session, scenario)
    with pytest.raises(AgentHostRunConflict) as refused:
        await admit(_spec(new_id, new_conversation, machine["harness_id"]))
    await db_session.rollback()
    assert "Ada's MacBook is shutting down or paused" in str(refused.value)
