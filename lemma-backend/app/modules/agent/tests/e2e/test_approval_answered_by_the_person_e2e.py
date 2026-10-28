"""An approval is answered by the person, never by the agent waiting on it (H5).

The sandbox an agent works in holds a delegated ``LEMMA_TOKEN`` whose user *is*
the person the agent is waiting on. The approval route took that token as the
person: an agent refused a delete could ask, then answer its own card with
``lemma`` from the same sandbox, and the transcript read as though the person
had approved it. Delegated tokens are now refused on the route outright,
whichever agent holds them -- the assistant or a named one.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select
from starlette import status

from app.core.api.actor_policy import WORKLOAD_NOT_ALLOWED
from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    ConversationStatus,
)
from app.modules.agent.domain.pauses import DECIDED
from app.modules.agent.infrastructure.models import AgentPauseModel
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    pausing_call,
    running_run,
    seeded_conversation,
)
from app.modules.test_support.e2e.builders import E2EScenario

# A decision resumes the run inline; the session worker must not race it.
pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _waiting_on_an_approval(scenario: E2EScenario) -> tuple[Seeded, str]:
    seeded = await seeded_conversation(scenario, toolsets=["POD", "USER_INTERACTION"])
    paused = await running_run(seeded)
    approval_id = await pausing_call(
        seeded,
        paused,
        tool_name="request_approval",
        tool_args={
            "tool_name": "pod_write_record",
            "args": {"action": "delete", "table_name": "chores", "record_id": "r1"},
            "title": "Delete the chore",
        },
    )
    await finish(paused, conversation_status=ConversationStatus.WAITING)
    return seeded, approval_id


def _decision_path(seeded: Seeded, approval_id: str) -> str:
    return (
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}"
        f"/approvals/{approval_id}/decision"
    )


async def _decisions(seeded: Seeded, approval_id: str) -> int:
    async with async_session_maker() as session:
        return (
            await session.execute(
                select(func.count()).where(
                    AgentPauseModel.conversation_id == seeded.conversation_id,
                    AgentPauseModel.tool_call_id == approval_id,
                    AgentPauseModel.status.in_(DECIDED),
                )
            )
        ).scalar_one()


@pytest.mark.parametrize("agent", ["named", "assistant"])
async def test_an_agent_cannot_approve_its_own_request(
    scenario: E2EScenario, agent: str
) -> None:
    seeded, approval_id = await _waiting_on_an_approval(scenario)
    owner = scenario.owner_user
    assert UUID(owner["id"]) == seeded.user_id
    # The token its sandbox holds: delegated by the very person it waits on.
    headers = (
        await scenario.delegated_agent_headers(
            user=owner, agent={"id": str(seeded.agent_id), "name": seeded.agent_name}
        )
        if agent == "named"
        else await scenario.default_pod_agent_headers(user=owner)
    )

    for decision in (
        AgentRunApprovalDecision.APPROVE_ONCE,
        AgentRunApprovalDecision.APPROVE_FOR_SESSION,
    ):
        response = await scenario.owner_client.post(
            _decision_path(seeded, approval_id),
            json={"decision": decision.value},
            headers=headers,
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
        assert response.json()["code"] == WORKLOAD_NOT_ALLOWED

    assert await _decisions(seeded, approval_id) == 0, "the refusal recorded nothing"


async def test_the_person_still_answers_their_own_approval(
    scenario: E2EScenario,
) -> None:
    seeded, approval_id = await _waiting_on_an_approval(scenario)

    response = await scenario.owner_client.post(
        _decision_path(seeded, approval_id),
        json={"decision": AgentRunApprovalDecision.DENY.value},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["decision"] == AgentRunApprovalDecision.DENY.value
    assert await _decisions(seeded, approval_id) == 1
