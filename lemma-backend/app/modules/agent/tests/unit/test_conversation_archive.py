"""The archive filter, and the rule for a title a person typed.

The listing half is asserted by compiling the statement rather than by running
it: what matters is that the default list narrows to unarchived rows at all,
and that asking for the archive is the *same* query with the other value --
not a second code path that can drift from the first.
"""

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent.domain.conversation_titles import (
    CONVERSATION_TITLE_MAX_LENGTH,
    normalize_conversation_title,
)
from app.modules.agent.domain.errors import ConversationValidationError
from app.modules.agent.domain.value_objects import ConversationAgentSelection
from app.modules.agent.infrastructure.repositories import ConversationRepository
import app.modules.agent.services.conversation_queries as queries


class _CapturingRepository:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def list_conversations(self, **kwargs):
        self.kwargs = kwargs
        return [], None


class _Result:
    def scalars(self):
        return []


class _Session:
    """Captures the statement instead of running it -- see
    `test_conversation_list_service`, which reads the whereclause the same way.
    Only the whereclause is compiled: compiling the whole select would need the
    mappers configured, which a unit test has no reason to arrange."""

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
@pytest.mark.parametrize("archived", [False, True])
async def test_list_filters_on_the_archive_flag(archived: bool) -> None:
    uow = _Uow()
    repository = ConversationRepository(uow)

    await repository.list_conversations(
        user_id=uuid4(),
        pod_id=uuid4(),
        agent_selection=ConversationAgentSelection.all(),
        archived=archived,
    )

    where_sql = str(
        uow.session.statement.whereclause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert f"agent_conversations.is_archived IS {str(archived).lower()}" in where_sql


@pytest.mark.asyncio
async def test_the_default_list_hides_the_archive(monkeypatch) -> None:
    """A caller that says nothing about archiving gets the unarchived list."""
    repository = _CapturingRepository()
    service = queries.ConversationQueries(None, repository, None)

    async def _resolve(_repo, *, pod_id, agent_name):
        return None

    monkeypatch.setattr(queries, "resolve_expected_agent_id", _resolve)
    monkeypatch.setattr(queries, "require_agent_action", _noop_permission)

    await service.list_conversations(
        pod_id=uuid4(),
        agent_selection=ConversationAgentSelection.all(),
        user_id=uuid4(),
    )

    assert repository.kwargs is not None
    assert repository.kwargs["archived"] is False


@pytest.mark.asyncio
async def test_the_archive_is_asked_for_explicitly(monkeypatch) -> None:
    repository = _CapturingRepository()
    service = queries.ConversationQueries(None, repository, None)

    async def _resolve(_repo, *, pod_id, agent_name):
        return None

    monkeypatch.setattr(queries, "resolve_expected_agent_id", _resolve)
    monkeypatch.setattr(queries, "require_agent_action", _noop_permission)

    await service.list_conversations(
        pod_id=uuid4(),
        agent_selection=ConversationAgentSelection.all(),
        user_id=uuid4(),
        archived=True,
    )

    assert repository.kwargs is not None
    assert repository.kwargs["archived"] is True


async def _noop_permission(*, user_id, pod_id, agent_id, action):
    return None


class TestConversationTitleRule:
    def test_keeps_a_typed_title_trimmed(self) -> None:
        assert normalize_conversation_title("  Tokyo food tour  ") == "Tokyo food tour"

    def test_blank_clears_rather_than_stores(self) -> None:
        # None is what `generate_title_if_absent` looks for, so clearing the
        # field is how a person hands the title back to the generator.
        assert normalize_conversation_title("") is None
        assert normalize_conversation_title("   ") is None
        assert normalize_conversation_title(None) is None

    def test_refuses_a_title_the_column_cannot_hold(self) -> None:
        longest = "t" * CONVERSATION_TITLE_MAX_LENGTH
        assert normalize_conversation_title(longest) == longest

        with pytest.raises(ConversationValidationError):
            normalize_conversation_title(longest + "t")

    def test_measures_length_after_trimming(self) -> None:
        longest = "t" * CONVERSATION_TITLE_MAX_LENGTH
        assert normalize_conversation_title(f"  {longest}  ") == longest
