"""A tap on a stale question, one answer of several, and the window a tap opens."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceInteraction,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.stale_interactions import (
    STALE_INTERACTION_TEXT,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _ask_user_link,
    _pending,
    _slack_event,
    _slack_surface,
    _surface_conversation,
    build_ingress_service,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio

_TWO_QUESTIONS = {
    "questions": [
        {
            "question": "Which size?",
            "header": "Size",
            "options": [{"label": "Small"}, {"label": "Large"}],
        },
        {
            "question": "Which colour?",
            "header": "Colour",
            "options": [{"label": "Blue"}, {"label": "Red"}],
        },
    ]
}


async def _tapped(*, values=None, decision=None):
    surface = _slack_surface()
    conversation_id = uuid4()
    event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, event)
    adapter = AsyncMock()
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.surface_conversation.return_value = _surface_conversation(
        surface, conversation_id=conversation_id
    )
    interaction = ParsedSurfaceInteraction(
        platform=SurfacePlatform.SLACK,
        external_channel_id=event.external_channel_id,
        external_thread_id=event.external_thread_id,
        external_user_id=link.external_user_id,
        callback_id=f"{conversation_id}|call-1",
        values=values or {},
        approval_decision=decision,
        dedup_id=f"tap-{uuid4().hex}",
    )
    await service.handle_interaction(interaction)
    return adapter, service.uow.session.execute, link


def _said(adapter) -> list[str]:
    return [
        call.kwargs["text"] for call in adapter.acknowledge_interaction.await_args_list
    ]


async def test_a_tap_on_a_question_no_longer_open_says_so():
    adapter, touched, link = await _tapped(decision="APPROVE_ONCE")

    assert _said(adapter) == [STALE_INTERACTION_TEXT]
    agent_conversations.resolve_pending_interaction.assert_not_awaited()
    # Still a tap from the person: their reply window reopened.
    stamped = touched.await_args_list[0].args[0]
    assert "last_inbound_at" in str(stamped)


async def test_one_answer_of_two_is_held_and_the_run_waits():
    pending = _pending("ask_user", tool_call_id="call-1", tool_args=_TWO_QUESTIONS)
    agent_conversations.pending_interaction.return_value = pending
    agent_conversations.pending_question.return_value = pending
    agent_conversations.merge_conversation_metadata_mapping.return_value = {
        "call-1|Size": "Small"
    }

    adapter, _, _ = await _tapped(values={"Size": "Small"})

    agent_conversations.resolve_pending_interaction.assert_not_awaited()
    assert _said(adapter) == ["Got it. One more question to answer."]


async def test_the_last_answer_resolves_with_every_answer_held():
    pending = _pending("ask_user", tool_call_id="call-1", tool_args=_TWO_QUESTIONS)
    agent_conversations.pending_interaction.return_value = pending
    agent_conversations.pending_question.return_value = pending
    agent_conversations.merge_conversation_metadata_mapping.return_value = {
        "call-1|Size": "Small",
        "call-1|Colour": "Blue",
    }

    await _tapped(values={"Colour": "Blue"})

    kwargs = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert kwargs["response"] == {"answers": {"Size": "Small", "Colour": "Blue"}}
    # And the held answers are let go of.
    agent_conversations.set_conversation_metadata_value.assert_awaited()
