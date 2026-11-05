"""A named agent refused a pod connector asks to be given it, and a yes does.

The pod's assistant reaches a person's apps with no setup; a named agent needs
``connector.use`` on the pod connector. Without it the refusal used to be a dead
end. Now it is a card -- "Let triage use mail (your own account)?" -- and
approving adds the grant to the agent, standing, conferred as the approver.

Real database, real authorizer, real decision endpoint: the parts that decide
whether a grant is written and whether the call then passes are not stood in.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    ConversationStatus,
)
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    pausing_call,
    running_run,
    seeded_conversation,
    uow_factory,
)
from app.modules.agent.tools.approval.executor import ApprovalExecutor
from app.modules.agent.tools.approval_denials import show_card
from app.modules.agent.tools.authority import workload_actor_id
from app.modules.agent.tools.connectors.models import RunConnectorOperationRequest
from app.modules.agent.tools.connectors.pydantic_adapter import (
    run_connector_operation,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.connectors.api.dependencies import get_connector_service
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_SLUG = "mail"


def _run_context(deps: BaseAgentContext):
    from pydantic_ai.tools import RunContext
    from pydantic_ai.usage import RunUsage

    return RunContext(deps=deps, model=None, usage=RunUsage(), prompt=None)


def _send() -> RunConnectorOperationRequest:
    return RunConnectorOperationRequest(
        connector=_SLUG, operation="send_email", payload={"to": "a@example.com"}
    )


async def _declare_mail(scenario: E2EScenario) -> None:
    """An installation with no provider behind it, declared as ``mail``."""
    connector_id = f"e2e-grant-mail-{uuid4().hex[:8]}"
    async with async_session_maker() as session:
        session.add(
            Connector(
                id=connector_id,
                title="Grant Mail",
                description="A connector a named agent asks to be given.",
                kinds=[{"kind": "http", "auth_scheme": "NOAUTH"}],
                is_active=True,
            )
        )
        session.add(
            ConnectorOperation(
                id=f"{connector_id}:send_email",
                connector_id=connector_id,
                name="send_email",
                provider_operation_name="SEND_EMAIL",
                display_name="Send Email",
                description="Send an email.",
                input_schema={
                    "type": "object",
                    "properties": {"to": {"type": "string"}},
                    "required": ["to"],
                },
                output_schema={"type": "object"},
            )
        )
        await session.commit()
        install = await get_connector_service(
            SqlAlchemyUnitOfWork(session)
        ).create_auth_config(
            user_id=UUID(scenario.owner_user["id"]),
            organization_id=UUID(scenario.org_id),
            connector_id=connector_id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={},
            name=f"grant-mail-{uuid4().hex[:8]}",
        )
        await session.commit()
    declared = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/connectors",
        json={"slug": _SLUG, "auth_config_id": str(install.id), "mode": "PER_MEMBER"},
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text


@pytest.fixture
async def asked(scenario: E2EScenario) -> Seeded:
    """A named agent with the connector toolset, in a pod that declares mail."""
    seeded = await seeded_conversation(
        scenario, toolsets=["CONNECTORS", "USER_INTERACTION"]
    )
    await _declare_mail(scenario)
    return seeded


def _deps(
    scenario: E2EScenario, seeded: Seeded, *, user_id: UUID | None = None
) -> BaseAgentContext:
    return BaseAgentContext(
        user_id=user_id or seeded.user_id,
        org_id=UUID(scenario.org_id),
        pod_id=seeded.pod_id,
        conversation_id=seeded.conversation_id,
        agent_name=seeded.agent_name,
        workload_id=seeded.agent_id,
    )


async def _granted(scenario: E2EScenario, seeded: Seeded) -> list[str]:
    response = await scenario.owner_client.get(
        f"/pods/{seeded.pod_id}/agents/{seeded.agent_name}/permissions"
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return [
        permission
        for grant in response.json()["grants"]
        if grant["resource_type"] == "connector" and grant["resource_name"] == _SLUG
        for permission in grant["permission_ids"]
    ]


async def _paused_on_the_card(seeded: Seeded, refused: dict) -> str:
    """The run asks with the refusal's own envelope and waits on the card."""
    run_id = await running_run(seeded)
    approval = refused["approval"]
    approval_id = await pausing_call(
        seeded,
        run_id,
        tool_name="request_approval",
        tool_args={
            "tool_name": approval["tool_name"],
            "args": approval["args"],
            "title": approval["title"],
            "permission_ids": approval["permission_ids"],
        },
    )
    await finish(run_id, conversation_status=ConversationStatus.WAITING)
    return approval_id


async def _decide(scenario, seeded: Seeded, approval_id: str, decision) -> None:
    response = await scenario.owner_client.post(
        f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}"
        f"/approvals/{approval_id}/decision",
        json={"decision": decision.value},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


async def test_a_named_agent_without_the_connector_is_given_a_card(
    scenario, asked
) -> None:
    refused = await run_connector_operation(
        _run_context(_deps(scenario, asked)), _send()
    )

    assert refused["needs_approval"] is True, refused
    assert refused["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"
    assert refused["approval"]["permission_ids"] == ["connector.use"]
    assert refused["approval"]["title"] == (
        f"Let {asked.agent_name} use {_SLUG} (your own account)?"
    )


async def test_approving_gives_the_agent_the_connector_and_the_call_goes_on(
    scenario, asked
) -> None:
    refused = await run_connector_operation(
        _run_context(_deps(scenario, asked)), _send()
    )
    approval_id = await _paused_on_the_card(asked, refused)

    await _decide(scenario, asked, approval_id, AgentRunApprovalDecision.APPROVE_ONCE)

    # The grant is written by the approval's work item, run after the
    # decision commits -- not inside the request -- so it is waited for.
    granted = await eventually(
        label="the approved connector grant",
        probe=lambda: _granted(scenario, asked),
        done=lambda permissions: permissions == ["connector.use"],
    )
    assert granted == ["connector.use"]
    # Standing, not a loan: the next call, unapproved, is past the grant. It
    # stops where any call of the owner's would -- they have no mail account.
    again = await run_connector_operation(_run_context(_deps(scenario, asked)), _send())
    assert "needs_approval" not in again, again
    assert again["code"] != "MISSING_WORKLOAD_RESOURCE_GRANT", again


async def test_declining_gives_nothing(scenario, asked) -> None:
    refused = await run_connector_operation(
        _run_context(_deps(scenario, asked)), _send()
    )
    approval_id = await _paused_on_the_card(asked, refused)

    await _decide(scenario, asked, approval_id, AgentRunApprovalDecision.DENY)

    assert await _granted(scenario, asked) == []
    again = await run_connector_operation(_run_context(_deps(scenario, asked)), _send())
    assert again["needs_approval"] is True, again


async def test_an_approver_who_could_not_use_it_cannot_give_it(scenario, asked) -> None:
    """Conferred as the approver, so bounded by them: a viewer, who may not
    use the pod's connectors, cannot hand one to an agent."""
    viewer = await scenario.create_user("grant-card-viewer")
    await scenario.add_user_to_pod(user=viewer, role="POD_VIEWER")
    refused = await run_connector_operation(
        _run_context(_deps(scenario, asked)), _send()
    )
    as_the_viewer = _deps(scenario, asked, user_id=UUID(viewer["id"]))
    approval_id = f"call-{uuid4().hex[:12]}"
    await show_card(
        conversation_id=asked.conversation_id,
        approval_id=approval_id,
        person_user_id=as_the_viewer.user_id,
        workload_actor_id=workload_actor_id(as_the_viewer),
        tool_name="run_connector_operation",
        args=refused["approval"]["args"],
    )

    result = await ApprovalExecutor(uow_factory()).execute_as_user(
        deps=as_the_viewer,
        tool_name="run_connector_operation",
        args=refused["approval"]["args"],
        approval_id=approval_id,
    )

    assert isinstance(result, dict) and result["success"] is False, result
    assert "You may not" in result["error"], result
    assert await _granted(scenario, asked) == []
