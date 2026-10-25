"""A decision on an Agent Host permission request reaches the host or nothing does.

The decision is two writes: the ``request_approval`` return the transcript
shows, and the ``RESOLVE_PERMISSION`` command the host collects. The command
used to be queued after the return committed, so a failure there left the card
closed and the transcript reading "Approved" over a request the host never
heard about -- and auto-denied half an hour later. Both now commit together.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostRunState,
)
from app.modules.agent.domain.agent_host_permissions import (
    permission_approval_events,
    permission_approval_tool_call_id,
)
from app.modules.agent.domain.value_objects import AgentEventType, MessageKind
from app.modules.agent.infrastructure.models import MessageModel
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.runtime_models import AgentHostCommandModel
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
)

pytestmark = pytest.mark.e2e

_REQUEST_ID = "call-rm"
_PAYLOAD = {
    "request_id": _REQUEST_ID,
    "tool_call_id": _REQUEST_ID,
    "tool": {"name": "exec_command", "source": "native", "title": "rm -rf build"},
    "input": {"cmd": "rm -rf build"},
    "options": [{"option_id": "once", "kind": "allow_once", "name": "Allow once"}],
}


async def _parked_permission(db_session, scenario) -> tuple[UUID, UUID]:
    machine = await paired_machine(scenario, display_name="permission e2e")
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        state=AgentHostRunState.RUNNING,
        title="permission",
    )
    call = next(
        event
        for event in permission_approval_events(
            agent_run_id=run_id,
            request_id=_REQUEST_ID,
            sequence=1,
            payload=_PAYLOAD,
            metadata={},
        )
        if event.type is AgentEventType.MESSAGE
    )
    await Messages(SqlAlchemyUnitOfWork(db_session)).append_message(
        conversation_id=conversation_id, agent_run_id=run_id, draft=call.data
    )
    await db_session.commit()
    return conversation_id, run_id


async def _written(db_session, conversation_id: UUID, run_id: UUID) -> tuple:
    """(the approval's tool return, the host's RESOLVE_PERMISSION command)."""
    db_session.expire_all()
    returned = (
        await db_session.execute(
            select(MessageModel).where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.kind == MessageKind.TOOL_RETURN.value,
                MessageModel.tool_call_id
                == permission_approval_tool_call_id(_REQUEST_ID),
            )
        )
    ).scalar_one_or_none()
    command = (
        await db_session.execute(
            select(AgentHostCommandModel).where(
                AgentHostCommandModel.run_id == run_id,
                AgentHostCommandModel.kind
                == AgentHostCommandKind.RESOLVE_PERMISSION.value,
            )
        )
    ).scalar_one_or_none()
    return returned, command


def _decision_url(scenario, conversation_id: UUID) -> str:
    return (
        f"/pods/{scenario.pod_id}/conversations/{conversation_id}/approvals/"
        f"{permission_approval_tool_call_id(_REQUEST_ID)}/decision"
    )


async def test_a_decision_the_host_cannot_be_given_is_not_written_either(
    db_session, scenario, monkeypatch: pytest.MonkeyPatch
):
    await scenario.create_org_with_pod(name_prefix="Permission decision")
    conversation_id, run_id = await _parked_permission(db_session, scenario)

    async def _unavailable(self, **_kwargs):
        raise OperationalError("INSERT", {}, ConnectionError("database went away"))

    # The database half of the decision fails, the way a connection dropping
    # mid-transaction does; everything the request runs through stays real.
    monkeypatch.setattr(
        "app.modules.agent.infrastructure.agent_host.dispatch_repository."
        "AgentHostDispatchRepository.enqueue_permission_decision",
        _unavailable,
    )
    with pytest.raises(OperationalError):
        await scenario.owner_client.post(
            _decision_url(scenario, conversation_id),
            json={"decision": "APPROVE_ONCE", "response": {}},
        )

    # Neither half: the card stays open rather than reading "Approved" over a
    # request the host was never given.
    assert await _written(db_session, conversation_id, run_id) == (None, None)


async def test_a_decision_the_host_is_given_is_written_with_it(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Permission decision")
    conversation_id, run_id = await _parked_permission(db_session, scenario)

    decided = await scenario.owner_client.post(
        _decision_url(scenario, conversation_id),
        json={"decision": "APPROVE_ONCE", "response": {}},
    )
    assert decided.status_code == 200, decided.text

    returned, command = await _written(db_session, conversation_id, run_id)
    assert returned is not None
    assert command is not None
    assert command.payload == {"request_id": _REQUEST_ID, "option_id": "once"}
