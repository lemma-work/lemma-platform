"""Two rapid first messages from one chat must end up on one conversation link.

The binder read "no link", then opened a conversation, then inserted. Two
messages arriving together both read "no link", so the second insert either hit
the unique index or -- where the channel or user id is NULL, which a plain unique
index never compares equal -- quietly made a second row, after which
`get_by_external_thread` (`scalar_one_or_none`) raised on every later message
from that person.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
    _thread_lock_key,
)
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _conversation,
    _slack_event,
    _slack_surface,
    build_doubles,
    conversation_operations,  # noqa: F401  (autouse fixture)
)
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder


def _route(surface) -> ResolvedSurfaceRoute:
    return ResolvedSurfaceRoute(
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        agent_name="Surface Agent",
        agent_display_name="Surface Agent",
        conversation_kind="DM",
        route_key="dm",
    )


def _binder(surface, *, existing_link=None):
    doubles = build_doubles(adapter=AsyncMock(), surfaces=[surface])
    doubles.conversation_link_repository.get_by_external_thread.return_value = None
    binder = ConversationBinder(
        uow=doubles.uow,
        surface_repository=doubles.surface_repository,
        conversation_link_repository=doubles.conversation_link_repository,
    )
    return binder, doubles.conversation_link_repository


async def test_the_chat_is_locked_before_a_link_is_created():
    surface = _slack_surface()
    user_id = uuid4()
    agent_conversations.open_surface_conversation.return_value = _conversation(
        surface, user_id
    )
    binder, links = _binder(surface)
    calls: list[str] = []
    links.lock_thread.side_effect = lambda **_: calls.append("lock")
    links.create.side_effect = lambda link, **_: calls.append("create") or link

    await binder.bind_conversation(
        surface=surface,
        parsed=_slack_event(),
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id, external_user_id="U123"
        ),
        route=_route(surface),
    )

    assert calls == ["lock", "create"]


async def test_the_loser_of_the_race_finds_the_winners_link_and_opens_nothing():
    """After the lock the winner's link is visible: use it, do not make another."""
    surface = _slack_surface()
    user_id = uuid4()
    event = _slack_event()
    winner = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform=surface.surface_type.value,
        external_channel_id=event.external_channel_id,
        external_thread_id=event.external_thread_id,
        external_user_id="U123",
        routed_agent_id=surface.agent_id,
        conversation_kind="DM",
        route_key="dm",
        last_event={},
    )
    binder, links = _binder(surface)
    # First read: nothing yet. Second read, after the lock: the winner committed.
    links.get_by_external_thread.side_effect = [None, winner]
    links.update_last_event.side_effect = lambda **_: winner

    link, created_title = await binder.bind_conversation(
        surface=surface,
        parsed=event,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id, external_user_id="U123"
        ),
        route=_route(surface),
    )

    assert link.id == winner.id
    assert created_title is None
    links.lock_thread.assert_awaited_once()
    links.create.assert_not_awaited()
    agent_conversations.open_surface_conversation.assert_not_awaited()


async def test_an_existing_link_takes_no_lock():
    surface = _slack_surface()
    event = _slack_event()
    existing = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform=surface.surface_type.value,
        external_channel_id=event.external_channel_id,
        external_thread_id=event.external_thread_id,
        external_user_id="U123",
        routed_agent_id=surface.agent_id,
        conversation_kind="DM",
        route_key="dm",
        last_event={},
    )
    binder, links = _binder(surface)
    links.get_by_external_thread.return_value = existing
    links.update_last_event.side_effect = lambda **_: existing

    await binder.bind_conversation(
        surface=surface,
        parsed=event,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(), external_user_id="U123"
        ),
        route=_route(surface),
    )

    links.lock_thread.assert_not_awaited()


# --- the repository ------------------------------------------------------


def _link(**overrides) -> AgentSurfaceConversationLink:
    fields = {
        "surface_id": uuid4(),
        "conversation_id": uuid4(),
        "platform": "TELEGRAM",
        "external_channel_id": None,
        "external_thread_id": "123",
        "external_user_id": None,
        "last_event": {},
    }
    fields.update(overrides)
    return AgentSurfaceConversationLink(**fields)


class _RecordingSession:
    """Answers each statement the repository issues, and remembers its kind."""

    def __init__(self, *, existing) -> None:
        self.kinds: list[str] = []
        self._existing = existing
        self.inserted = _link()

    async def execute(self, statement, *_):
        text_form = str(
            statement.compile(dialect=postgresql.dialect())
            if hasattr(statement, "compile")
            else statement
        )
        if "pg_advisory_xact_lock" in text_form:
            self.kinds.append("lock")
            return SimpleNamespace()
        if "INSERT" in text_form:
            self.kinds.append("insert")
            return SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(
                    to_entity=lambda: self.inserted
                )
            )
        self.kinds.append("read")
        return SimpleNamespace(
            scalar_one_or_none=lambda: (
                SimpleNamespace(to_entity=lambda: self._existing)
                if self._existing is not None
                else None
            )
        )


async def test_create_takes_the_chat_lock_before_it_reads_or_inserts():
    """Every creator is covered, notification delivery included: the unique index
    cannot say "one link per chat" for a chat with no channel or user."""
    session = _RecordingSession(existing=None)
    session.sync_session = SimpleNamespace(info={})
    repository = SurfaceConversationLinkRepository(SimpleNamespace(session=session))

    await repository.create(_link())

    assert session.kinds[0] == "lock"
    assert "insert" in session.kinds


async def test_create_hands_back_the_link_that_won_instead_of_inserting_another():
    winner = _link()
    session = _RecordingSession(existing=winner)
    session.sync_session = SimpleNamespace(info={})
    repository = SurfaceConversationLinkRepository(SimpleNamespace(session=session))

    created = await repository.create(_link())

    assert created is winner
    assert "insert" not in session.kinds


def test_the_thread_lock_tells_a_missing_channel_from_an_empty_one():
    surface_id = uuid4()
    key = _thread_lock_key
    assert key(surface_id, "TELEGRAM", None, "1", None) == key(
        surface_id, "TELEGRAM", None, "1", None
    )
    assert key(surface_id, "TELEGRAM", None, "1", None) != key(
        surface_id, "TELEGRAM", "", "1", None
    )
    assert key(surface_id, "TELEGRAM", None, "1", None) != key(
        surface_id, "TELEGRAM", None, "1", "77"
    )
