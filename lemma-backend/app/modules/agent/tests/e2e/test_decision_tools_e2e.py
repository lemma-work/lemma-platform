"""E2E: the pod's assistant saves a decider, decides a file, and answers a decision.

Nothing below the tools is faked: the dispatcher assembles the assistant's own
toolset, and the tools run against the real authorization service, datastore
and decisions module on a real database. Every question is answered by a rule,
so no engine is ever asked -- e2e runs with no Typesafe key and a mocked model,
and a rule is the one rung whose answer is the same every time.
"""

from __future__ import annotations

import csv
import io
from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_TRIAGE = {
    "description": "Whether a ticket is urgent, by the words in its subject.",
    "input": {"fields": ["subject"]},
    "questions": {"urgent": {"type": "yes_no", "prompt": "Is this ticket urgent?"}},
    "rules": [
        {"when": "contains(subject, 'URGENT')", "answer": {"urgent": True}},
        {"when": "`true`", "answer": {"urgent": False}},
    ],
}


def _assistant(scenario) -> BaseAgentContext:
    """The pod's own assistant, addressed by its pod's id as a run addresses it."""
    pod_id = UUID(scenario.pod_id)
    return BaseAgentContext(
        user_id=UUID(scenario.owner_user["id"]),
        org_id=UUID(scenario.org_id),
        pod_id=pod_id,
        conversation_id=uuid4(),
        workload_type="agent",
        workload_id=pod_id,
        is_pod_default_agent=True,
    )


async def _call(ctx: BaseAgentContext, tool: str, **arguments: object) -> dict:
    """A tool call as the MCP bridge and the approval executor make one: the
    dispatcher resolves the assistant's toolsets itself."""
    result = await AgentToolDispatcher(
        SessionUnitOfWorkFactory(async_session_maker)
    ).call_tool(ctx=ctx, name=tool, arguments=arguments)
    assert isinstance(result, dict)
    return result


async def test_the_assistant_saves_a_decider_and_decides_a_file_by_its_rules(
    scenario,
) -> None:
    await scenario.create_org_with_pod(name_prefix="Decisions")
    ctx = _assistant(scenario)
    tickets = "id,subject\n" + "".join(
        f"{n},{'URGENT: checkout is down' if n % 3 == 0 else 'hello'}\n"
        for n in range(1, 31)
    )
    upload = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/datastore/files",
        data={"directory_path": "/me/triage", "search_enabled": "false"},
        files={"data": ("tickets.csv", tickets.encode(), "text/csv")},
    )
    assert upload.status_code == status.HTTP_201_CREATED, upload.text

    saved = await _call(
        ctx, "define_decider", name="ticket-urgency", definition=_TRIAGE
    )
    decided = await _call(
        ctx, "decide", decider="ticket-urgency", file="/me/triage/tickets.csv", key="id"
    )

    assert (saved["success"], saved["version"]) == (True, 1), saved
    assert decided["success"] is True, decided
    assert decided["counts"] == {"urgent": {"true": 10, "false": 20}}
    assert decided["answered_by"] == {"rules": 30}
    path = decided["results_file"]["pod_path"]
    assert path.startswith("/me/triage/tickets.decided-")
    read = await _call(ctx, "pod_read_file", path=path)
    rows = list(csv.DictReader(io.StringIO(read["text"])))
    assert [row["urgent"] for row in rows[:3]] == ["false", "false", "true"]
    assert all(row["decision_id"] for row in rows)


async def test_an_agents_answer_is_recorded_as_its_own_and_teaches_nothing(
    scenario,
) -> None:
    await scenario.create_org_with_pod(name_prefix="Decisions")
    ctx = _assistant(scenario)

    asked = await _call(ctx, "decide", definition=_TRIAGE, state={"subject": "hello"})
    answered = await _call(
        ctx,
        "answer_decision",
        decision_id=asked["decision_id"],
        answers={"urgent": True},
    )

    assert asked["answers"]["urgent"] == {"value": False, "by": "rules"}, asked
    assert answered["answers"]["urgent"] == {"value": True, "by": "agent"}, answered
    assert answered["status"] == "corrected"
    record = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/decisions/{asked['decision_id']}"
    )
    assert record.status_code == status.HTTP_200_OK, record.text
    assert record.json()["answers"]["urgent"]["by"] == "agent"
    async with async_session_maker() as session:
        examples = await session.scalar(
            text("SELECT count(*) FROM decision_examples WHERE decision_id = :id"),
            {"id": UUID(asked["decision_id"])},
        )
    assert examples == 0
