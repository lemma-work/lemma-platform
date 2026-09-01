"""An agent cannot be edited into having no instruction.

`Agent.instruction` is a `str`, and the column behind it is NOT NULL. The blank
check on `update_agent` used to read `if instruction is not None and not
instruction.strip()`, which rejected `""` and `"   "` while letting an explicit
`None` through to `agent.instruction = None`. Pydantic does not validate on
assignment, so that succeeded in memory and the null travelled all the way to
the write, where it could only fail later and worse than a 400.

Only an explicit `"instruction": null` ever reached this. The controller builds
its keyword arguments with `model_dump(exclude_unset=True)`, so a PATCH that
simply omits the field never mentions it at all -- which is the case the last
test here pins, because it is the one a reader will worry the fix broke.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.errors import AgentValidationError
from app.modules.agent.services.agent_service import AgentService
from app.modules.test_support.authz import allow_all_context

pytestmark = pytest.mark.unit


def _service(monkeypatch, agent):
    repository = AsyncMock()
    repository.update.return_value = agent
    service = AgentService(
        uow=AsyncMock(),
        agent_repository=repository,
        authorization_service=AsyncMock(),
    )
    monkeypatch.setattr(service, "get_agent_by_name", AsyncMock(return_value=agent))
    # The memory grant is `create`/`update`'s own concern and has its own file.
    monkeypatch.setattr(
        "app.composition.agent_memory.derive_agent_memory_grant", AsyncMock()
    )
    return service, repository


def _agent():
    # `kind` is read before the instruction rules are: the pod's own assistant
    # is refused outright rather than validated. A double without it asserts
    # against a shape no real agent has.
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        name="reporter",
        kind=AgentKind.USER,
        instruction="Report on the week.",
        toolsets=[],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cleared", [None, "", "   ", "\n\t"])
async def test_an_instruction_cannot_be_cleared(monkeypatch, cleared) -> None:
    """`None` is rejected alongside blank, not accepted as "leave it alone"."""
    agent = _agent()
    service, repository = _service(monkeypatch, agent)

    with pytest.raises(AgentValidationError):
        await service.update_agent(
            pod_id=uuid4(),
            name="reporter",
            instruction=cleared,
            ctx=allow_all_context(),
        )

    repository.update.assert_not_awaited()
    assert agent.instruction == "Report on the week.", (
        "the agent was mutated before the validation error, so a caller that "
        "catches it holds an entity that no longer matches the row"
    )


@pytest.mark.asyncio
async def test_an_instruction_that_says_something_is_written(monkeypatch) -> None:
    agent = _agent()
    service, repository = _service(monkeypatch, agent)

    await service.update_agent(
        pod_id=uuid4(),
        name="reporter",
        instruction="Report on the month.",
        ctx=allow_all_context(),
    )

    assert agent.instruction == "Report on the month."
    repository.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_update_that_never_mentions_the_instruction_keeps_it(
    monkeypatch,
) -> None:
    """The PATCH the controller actually sends when the field is omitted.

    `instruction` defaults to `UNSET`, not `None`, so an edit to some other
    field must not travel down the validation branch at all.
    """
    agent = _agent()
    service, repository = _service(monkeypatch, agent)

    await service.update_agent(
        pod_id=uuid4(),
        name="reporter",
        description="Just the description.",
        ctx=allow_all_context(),
    )

    assert agent.instruction == "Report on the week."
    assert agent.description == "Just the description."
    repository.update.assert_awaited_once()
