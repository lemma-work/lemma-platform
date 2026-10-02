"""An answer to a message typed in Lemma is not threaded under the chat's last line.

An outbound is built from the thread's last inbound. When the run sending it was
started by a member typing in Lemma, the group never saw what it answers, and
quoting the last thing said there made the answer read as a reply to whoever
spoke last -- a stranger, in the screenshot that found it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.telegram.message_experience import (
    reply_parameters,
)
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery

pytestmark = pytest.mark.unit

_CONVERSATIONS = "app.modules.agent.contracts.conversations_for_surfaces"
_LAST_INBOUND = datetime(2026, 10, 2, 11, 30, tzinfo=timezone.utc)


def _group_message() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id="-100",
        external_thread_id="-100",
        sender_external_user_id="42",
        sender_display_name="P C",
        message_text="what all do we have",
        reply_target={"chat_id": "-100", "message_id": "77"},
    )


@pytest.fixture(autouse=True)
def conversation(monkeypatch):
    """The conversation's owner, doubled on `agent`'s contract: it reads a row."""
    monkeypatch.setattr(
        f"{_CONVERSATIONS}.surface_conversation",
        AsyncMock(return_value=SimpleNamespace(user_id=uuid4(), pod_id=uuid4())),
    )


@pytest.fixture
def lemma_run_started(monkeypatch):
    """When the conversation's latest run started, if it answers Lemma."""
    started = AsyncMock(return_value=None)
    monkeypatch.setattr(f"{_CONVERSATIONS}.lemma_message_run_started_at", started)
    return started


def _delivery(*, kind: str) -> SurfaceDelivery:
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.TELEGRAM,
        account_id=uuid4(),
        config=SurfaceConfig(),
    )
    surfaces = AsyncMock()
    surfaces.get.return_value = surface
    links = AsyncMock()
    links.get_by_conversation_id.return_value = AgentSurfaceConversationLink(
        id=uuid4(),
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="-100",
        external_thread_id="-100",
        conversation_kind=kind,
        last_event=_group_message().model_dump(mode="json"),
        last_inbound_at=_LAST_INBOUND,
    )
    return SurfaceDelivery(
        uow=SimpleNamespace(session=None),
        surface_repository=surfaces,
        conversation_link_repository=links,
        adapter_registry=SimpleNamespace(get=lambda platform: AsyncMock()),
        credential_resolver=SimpleNamespace(
            for_surface=AsyncMock(return_value={"bot_token": "t"})
        ),
    )


async def _event(*, kind: str = "CHANNEL") -> ParsedInboundSurfaceEvent:
    target = await _delivery(kind=kind).resolve_egress_target(uuid4())
    assert target is not None
    return target.event


async def test_an_answer_to_lemma_quotes_nobody(lemma_run_started):
    lemma_run_started.return_value = _LAST_INBOUND + timedelta(minutes=5)

    event = await _event()

    assert event.answers_inbound is False
    assert reply_parameters(event) is None


async def test_somebody_writing_since_is_part_of_what_it_answers(lemma_run_started):
    lemma_run_started.return_value = _LAST_INBOUND - timedelta(minutes=5)

    event = await _event()

    assert event.answers_inbound is True
    assert reply_parameters(event) == {
        "message_id": 77,
        "allow_sending_without_reply": True,
    }


async def test_an_answer_to_the_chat_still_quotes_it(lemma_run_started):
    event = await _event()

    assert event.answers_inbound is True
    assert reply_parameters(event) is not None


async def test_a_direct_chat_never_asks(lemma_run_started):
    """Only a group's conversation can hold a run started from Lemma."""
    await _event(kind="DM")

    lemma_run_started.assert_not_awaited()
