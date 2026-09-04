"""Every caller who makes an agent gets the folder its toolset implies.

The bug this file exists for: `sync_memory_folder_grant` was called from the
agent HTTP controller and nowhere else. An agent created straight through
`AgentService` -- which is what the pod bundle applier does -- got MEMORY in its
toolsets and no `/memory` folder to write to.

Dormant until MEMORY became a default for new agents (#476), and then fatal:
the source agent held `folder:/memory`, the export recorded the grant, and
applying it against a pod where nothing had ever created that folder failed the
whole import with `Unknown resource name(s): folder:/memory` -- reported to the
user only as "Apply failed due to a transient error".

Patching the composition wrapper rather than the derivation itself: what these
assert is that the *service* asks, which is the layering that was missing. What
the derivation then does is `agent_memory_grant`'s own business and is tested
there.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.services.agent_service import AgentService
from app.modules.test_support.authz import allow_all_context

pytestmark = pytest.mark.unit


def _service(agent, *, exists: bool):
    repository = AsyncMock()
    repository.create.return_value = agent
    repository.update.return_value = agent
    # Create checks for a clash first; update has to find the agent it edits.
    repository.get_by_pod_and_name.return_value = agent if exists else None
    # The service's own uow, not one reached through the repository. Handed back
    # so the tests can assert the derivation is passed *this* one: an `AsyncMock`
    # repository answers `.uow` with a fresh mock whatever happens, so nothing
    # here would notice the service reading the wrong one.
    uow = AsyncMock()
    return (
        AgentService(
            uow=uow, agent_repository=repository, authorization_service=AsyncMock()
        ),
        repository,
        uow,
    )


def _agent(toolsets):
    made = AsyncMock()
    made.id = uuid4()
    made.user_id = uuid4()
    made.name = "reporter"
    made.toolsets = toolsets
    return made


@pytest.mark.asyncio
async def test_creating_an_agent_derives_its_memory_grant(monkeypatch):
    agent = _agent([AgentToolset.MEMORY])
    service, _, uow = _service(agent, exists=False)
    asked: dict = {}

    async def _derive(derive_uow, **kwargs):
        asked["uow"] = derive_uow
        asked.update(kwargs)

    monkeypatch.setattr(
        "app.modules.agent.services.agent_memory_grant.derive_agent_memory_grant",
        _derive,
    )
    monkeypatch.setattr(
        "app.composition.agent_email_surface.provision_agent_email_surface",
        AsyncMock(return_value=None),
    )

    await service.create_agent(
        pod_id=uuid4(),
        user_id=uuid4(),
        name="reporter",
        instruction="Report.",
        toolsets=[AgentToolset.MEMORY],
        ctx=allow_all_context(),
    )

    assert asked, "the service created an agent without deriving its memory grant"
    assert asked["toolsets"] == [AgentToolset.MEMORY]
    assert asked["uow"] is uow


@pytest.mark.asyncio
async def test_updating_derives_from_the_agent_as_saved_not_from_the_request(
    monkeypatch,
):
    # A PATCH that omits `toolsets` is not the same thing as one turning memory
    # off. Reading the request would revoke the grant on any unrelated edit.
    agent = _agent([AgentToolset.MEMORY])
    service, _, uow = _service(agent, exists=True)
    asked: dict = {}

    async def _derive(derive_uow, **kwargs):
        asked["uow"] = derive_uow
        asked.update(kwargs)

    monkeypatch.setattr(
        "app.modules.agent.services.agent_memory_grant.derive_agent_memory_grant",
        _derive,
    )

    await service.update_agent(
        pod_id=uuid4(),
        name="reporter",
        description="Just the description.",
        ctx=allow_all_context(),
    )

    assert asked["toolsets"] == [AgentToolset.MEMORY]
    assert asked["uow"] is uow
