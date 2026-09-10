"""Provider session establishment precedes answers and survives stale polls."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AGENT_HOST_SESSION_METADATA_KEY,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.infrastructure.agent_host.control_updates import (
    apply_control_updates,
)
from app.modules.agent.infrastructure.agent_host.event_stream import StreamedEvent
from app.modules.agent.infrastructure.agent_host.session_memory import (
    remember_provider_session,
    record_pending_instructions,
    resume_session_id,
)
from app.modules.agent.infrastructure.harnesses.agent_host.events import (
    AgentHostEventNormalizer,
)
from app.modules.agent.infrastructure.harnesses.agent_host.harness import RemoteHarness
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
)
from app.modules.test_support.e2e.builders import E2EScenario

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "host_cwd",
    ["/Users/test/Projects/Δ workspace", r"C:\Users\test\Projects\Δ workspace"],
)
async def test_session_start_is_saved_before_answer_and_late_poll_cannot_replace_it(
    db_session: AsyncSession,
    scenario: E2EScenario,
    host_cwd: str,
) -> None:
    await scenario.create_org_with_pod(name_prefix="SessionStart")
    machine = await paired_machine(scenario, harness_key="claude-code")
    conversation_id, old_run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    old = AgentHostRunCheckpoint(
        run_id=old_run_id,
        lease_epoch=1,
        state=AgentHostRunState.DISPATCHING,
        detail={"provider_session_id": "expired-session"},
    )
    await record_pending_instructions(
        uow,
        conversation_id=conversation_id,
        run_id=old_run_id,
        digest="old-instructions",
    )
    await remember_provider_session(
        uow, old.model_copy(update={"state": AgentHostRunState.RUNNING})
    )
    await ConversationRepository(uow).set_conversation_metadata_key(
        conversation_id, "cwd", "/workspace/c/test/sandbox"
    )
    previous = await db_session.get(AgentRunModel, old_run_id)
    assert previous is not None
    previous.status = "FAILED"
    previous.finished_at = previous.created_at
    await db_session.flush()
    now = previous.created_at + timedelta(seconds=1)
    new_run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        created_at=now,
        started_at=now,
    )
    db_session.add(new_run)
    await db_session.flush()
    db_session.add(
        AgentHostRunLeaseModel(
            run_id=new_run.id,
            host_id=machine["host_id"],
            harness_id=machine["harness_id"],
            lease_epoch=1,
            state="ACCEPTED",
            accepted_at=now,
            lease_expires_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.flush()
    await record_pending_instructions(
        uow,
        conversation_id=conversation_id,
        run_id=new_run.id,
        digest="new-instructions",
    )
    conversation = await ConversationRepository(uow).get_conversation(conversation_id)
    assert conversation is not None
    harness = RemoteHarness(lambda: SqlAlchemyUnitOfWork(db_session))
    await harness._normalize(
        AgentHostEventNormalizer(agent_run_id=new_run.id, model_name="claude"),
        StreamedEvent(
            stream_id="1-0",
            sequence=1,
            type="run_state",
            object_id=None,
            payload={
                "state": "DISPATCHING",
                "provider_session_id": "renewed-session",
                "host_cwd": host_cwd,
            },
        ),
        ctx=AgentContext(
            user_id=UUID(scenario.owner_user["id"]),
            pod_id=scenario.pod_id,
            conversation_id=conversation_id,
        ),
        conversation=conversation,
        agent_run_id=new_run.id,
    )
    # No assistant event, terminal event, or control checkpoint has arrived.
    assert (
        await resume_session_id(
            uow,
            conversation_id=conversation_id,
            harness_id=machine["harness_id"],
            capabilities={"load_session": True},
        )
        == "renewed-session"
    )
    assert not await remember_provider_session(uow, old)
    stored = await ConversationRepository(uow).get_conversation_metadata_key(
        conversation_id, AGENT_HOST_SESSION_METADATA_KEY
    )
    assert isinstance(stored, dict)
    assert stored["session_id"] == "renewed-session"
    assert stored["run_id"] == str(new_run.id)
    assert stored["host_cwd"] == host_cwd
    assert stored["host_id"] == str(machine["host_id"])
    assert "instructions_digest" not in stored
    assert stored["pending_instructions"]["digest"] == "new-instructions"
    assert (
        await ConversationRepository(uow).get_conversation_metadata_key(
            conversation_id, "cwd"
        )
        == "/workspace/c/test/sandbox"
    )
    assert (
        await remember_provider_session(
            uow,
            AgentHostRunCheckpoint(
                run_id=new_run.id,
                lease_epoch=1,
                state=AgentHostRunState.RUNNING,
                detail={"provider_session_id": "renewed-session"},
            ),
        )
        is True
    )
    stored = await ConversationRepository(uow).get_conversation_metadata_key(
        conversation_id, AGENT_HOST_SESSION_METADATA_KEY
    )
    assert stored["host_cwd"] == host_cwd
    assert stored["instructions_digest"] == "new-instructions"
    assert "pending_instructions" not in stored


async def test_another_host_or_stale_epoch_cannot_write_a_session_binding(
    db_session: AsyncSession,
    scenario: E2EScenario,
) -> None:
    await scenario.create_org_with_pod(name_prefix="SessionFence")
    machine = await paired_machine(scenario)
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    for host_id, epoch in [(uuid4(), 1), (machine["host_id"], 2)]:
        await apply_control_updates(
            db_session,
            uow,
            host_id=host_id,
            checkpoints=[
                AgentHostRunCheckpoint(
                    run_id=run_id,
                    lease_epoch=epoch,
                    state=AgentHostRunState.DISPATCHING,
                    detail={"provider_session_id": "must-not-bind"},
                )
            ],
            rejections=[],
            now=datetime.now(timezone.utc),
            lease_seconds=60,
        )
        assert (
            await ConversationRepository(uow).get_conversation_metadata_key(
                conversation_id, AGENT_HOST_SESSION_METADATA_KEY
            )
            is None
        )
