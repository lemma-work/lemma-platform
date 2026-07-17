from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.services.conversation_service import ConversationService


class _ConversationRepository:
    def __init__(self) -> None:
        self.kwargs = None

    async def list_conversations(self, **kwargs):
        self.kwargs = kwargs
        return [], None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_name", "include_all_agents", "expected_agent_id", "filter_by_agent"),
    [
        (None, True, None, False),
        (None, False, None, True),
        ("researcher", False, "resolved", True),
    ],
)
async def test_list_conversations_forwards_tri_state_agent_filter(
    agent_name,
    include_all_agents,
    expected_agent_id,
    filter_by_agent,
) -> None:
    repository = _ConversationRepository()
    resolved_agent_id = uuid4()
    service = ConversationService.__new__(ConversationService)
    service.conversation_repository = repository
    service._expected_agent_id = AsyncMock(
        side_effect=lambda *, pod_id, agent_name: (
            resolved_agent_id if agent_name is not None else None
        )
    )
    service._require_agent_action = AsyncMock()

    await service.list_conversations(
        pod_id=uuid4(),
        agent_name=agent_name,
        include_all_agents=include_all_agents,
        user_id=uuid4(),
    )

    resolved_expected_agent_id = (
        resolved_agent_id if expected_agent_id == "resolved" else expected_agent_id
    )
    assert repository.kwargs["agent_id"] == resolved_expected_agent_id
    assert repository.kwargs["filter_by_agent"] is filter_by_agent
    assert service._expected_agent_id.await_count == (0 if include_all_agents else 1)
