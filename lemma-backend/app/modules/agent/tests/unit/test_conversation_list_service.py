from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent.domain.value_objects import (
    ConversationAgentScope,
    ConversationAgentSelection,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.conversation_service import ConversationService


class _ConversationRepository:
    def __init__(self) -> None:
        self.kwargs = None

    async def list_conversations(self, **kwargs):
        self.kwargs = kwargs
        return [], None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selection", "expected_scope", "expected_agent_id", "resolve_count"),
    [
        (ConversationAgentSelection.all(), ConversationAgentScope.ALL, None, 1),
        (
            ConversationAgentSelection.pod_default(),
            ConversationAgentScope.POD_DEFAULT,
            None,
            1,
        ),
        (
            ConversationAgentSelection.named("researcher"),
            ConversationAgentScope.NAMED,
            "resolved",
            1,
        ),
    ],
)
async def test_list_conversations_resolves_agent_selection(
    selection,
    expected_scope,
    expected_agent_id,
    resolve_count,
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
        agent_selection=selection,
        user_id=uuid4(),
    )

    resolved_expected_agent_id = (
        resolved_agent_id if expected_agent_id == "resolved" else expected_agent_id
    )
    repository_selection = repository.kwargs["agent_selection"]
    assert repository_selection.scope is expected_scope
    assert repository_selection.value == resolved_expected_agent_id
    assert service._expected_agent_id.await_count == resolve_count


class _Result:
    def scalars(self):
        return []


class _Session:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result()


class _Uow:
    def __init__(self) -> None:
        self.session = _Session()

    def collect_events(self, events) -> None:
        _ = events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        ConversationAgentSelection.all(),
        ConversationAgentSelection.pod_default(),
        ConversationAgentSelection.named(uuid4()),
    ],
)
@pytest.mark.parametrize("parent_id", [None, uuid4()])
async def test_repository_applies_agent_selection_to_roots_and_children(
    selection,
    parent_id,
) -> None:
    uow = _Uow()
    repository = ConversationRepository(uow)

    await repository.list_conversations(
        user_id=uuid4(),
        pod_id=uuid4(),
        agent_selection=selection,
        parent_id=parent_id,
    )

    where_sql = str(
        uow.session.statement.whereclause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    if selection.scope is ConversationAgentScope.ALL:
        assert "coalesce(agent_conversations.agent_id" not in where_sql
    else:
        assert "coalesce(agent_conversations.agent_id" in where_sql
        expected_agent_id = (
            "00000000-0000-0000-0000-000000000001"
            if selection.scope is ConversationAgentScope.POD_DEFAULT
            else str(selection.value)
        )
        assert expected_agent_id in where_sql
