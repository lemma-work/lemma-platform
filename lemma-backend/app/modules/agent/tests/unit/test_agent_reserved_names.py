from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.errors import AgentValidationError
from app.modules.agent.services.agent_service import AgentService
from app.modules.test_support.authz import allow_all_context


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["POD_DEFAULT", "pod_default"])
async def test_create_agent_rejects_pod_default_selector_names(name: str) -> None:
    repository = AsyncMock()
    service = AgentService(
        agent_repository=repository,
        authorization_service=AsyncMock(),
    )

    with pytest.raises(AgentValidationError, match="reserved"):
        await service.create_agent(
            pod_id=uuid4(),
            user_id=uuid4(),
            name=name,
            instruction="Help with this pod.",
            ctx=allow_all_context(),
        )

    repository.create.assert_not_awaited()
