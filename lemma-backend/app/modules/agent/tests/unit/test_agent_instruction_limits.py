"""Instruction limits apply to HTTP requests and non-HTTP authoring callers."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent.api.schemas import CreateAgentRequest, UpdateAgentRequest
from app.modules.agent.domain.entities import Agent, validate_agent_instruction
from app.modules.agent.domain.errors import AgentValidationError
from app.modules.agent.services.agent_service import AgentService
from app.modules.test_support.authz import allow_all_context

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("character", ["x", "界", "🙂"])
@pytest.mark.parametrize("length", [59_999, 60_000, 60_001])
def test_instruction_budget_counts_characters(character: str, length: int) -> None:
    instruction = character * length
    if length > 60_000:
        with pytest.raises(AgentValidationError, match="60,000 characters"):
            validate_agent_instruction(instruction)
        with pytest.raises(ValidationError, match="60000 characters"):
            CreateAgentRequest(name="helper", instruction=instruction)
        with pytest.raises(ValidationError, match="60000 characters"):
            UpdateAgentRequest(instruction=instruction)
    else:
        assert validate_agent_instruction(instruction) == instruction
        assert (
            CreateAgentRequest(name="helper", instruction=instruction).instruction
            == instruction
        )
        assert UpdateAgentRequest(instruction=instruction).instruction == instruction


@pytest.mark.parametrize("instruction", [None, "", " \n\t"])
def test_instruction_still_requires_nonblank_text(instruction: str | None) -> None:
    with pytest.raises(AgentValidationError, match="required"):
        validate_agent_instruction(instruction)


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_non_http_callers_cannot_bypass_the_limit(operation: str) -> None:
    agent = Agent(
        id=uuid4(), pod_id=uuid4(), user_id=uuid4(), name="helper", instruction="Saved."
    )
    repository = AsyncMock()
    repository.get_by_pod_and_name.return_value = (
        agent if operation == "update" else None
    )
    service = AgentService(
        uow=AsyncMock(), agent_repository=repository, authorization_service=AsyncMock()
    )
    with pytest.raises(AgentValidationError, match="60,000 characters"):
        if operation == "create":
            await service.create_agent(
                pod_id=agent.pod_id,
                user_id=agent.user_id,
                name=agent.name,
                instruction="x" * 60_001,
                ctx=allow_all_context(),
            )
        else:
            await service.update_agent(
                pod_id=agent.pod_id,
                name=agent.name,
                instruction="x" * 60_001,
                ctx=allow_all_context(),
            )
    repository.create.assert_not_awaited()
    repository.update.assert_not_awaited()
    assert agent.instruction == "Saved."
