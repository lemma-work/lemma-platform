"""What a person is told after tapping a card, given what actually happened.

The tap is a request. By the time it arrives the pause may have been denied on
the person's behalf (they typed something else instead), the decision may be
waiting on a worker job, the conversation may have lost the thread, or the
token the button carried may not be readable right now. Each of those used to
read as "Done" or "expired" -- and one of them told somebody who pressed
Approve that it was done when it had been cancelled.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.conversations_for_surfaces import (
    AgentRunApprovalDecision,
    ApprovalResolution,
)
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceInteraction,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
)
from app.modules.agent_surfaces.services.interaction_acks import acknowledgement_for
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _answered_in_thread,
    _slack_event,
    _slack_surface,
    _surface_conversation,
    _telegram_surface,
    build_ingress_service,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio

APPROVE = AgentRunApprovalDecision.APPROVE_ONCE
DENY = AgentRunApprovalDecision.DENY


@pytest.mark.parametrize(
    "tapped, stands, status, said",
    [
        (APPROVE, APPROVE, "resolved", "Done"),
        (DENY, DENY, "resolved", "Done"),
        (APPROVE, APPROVE, "queued", "Approved — running it now."),
        (APPROVE, DENY, "reconciled", "already cancelled because you sent"),
        (
            AgentRunApprovalDecision.APPROVE_FOR_SESSION,
            DENY,
            "reconciled",
            "already cancelled",
        ),
        (DENY, APPROVE, "reconciled", "already approved"),
        (AgentRunApprovalDecision.APPROVE_FOR_SESSION, APPROVE, "resolved", "Done"),
    ],
)
async def test_the_words_follow_the_decision_that_stands(
    tapped, stands, status, said
) -> None:
    text = acknowledgement_for(tapped, ApprovalResolution(status, stands))
    assert said in text


async def test_a_conversation_deleted_under_the_tap_says_so() -> None:
    assert "gone" in acknowledgement_for(APPROVE, None)


async def _tapped(*, stands: AgentRunApprovalDecision, status: str = "reconciled"):
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = _answered_in_thread(surface, conversation_id, parsed_event)
    adapter = _adapter()
    service = build_ingress_service(adapter=adapter, surfaces=[surface], answered=link)
    agent_conversations.surface_conversation.return_value = _surface_conversation(
        surface, conversation_id=conversation_id
    )

    async def stored(_uow, **_):
        return ApprovalResolution(status, stands)

    agent_conversations.resolve_pending_interaction.side_effect = stored
    await service.handle_interaction(
        ParsedSurfaceInteraction(
            platform=SurfacePlatform.SLACK,
            external_channel_id=parsed_event.external_channel_id,
            external_thread_id=parsed_event.external_thread_id,
            external_user_id=link.external_user_id,
            callback_id=f"{conversation_id}|call-1",
            approval_decision="APPROVE_ONCE",
            dedup_id=f"tap-{uuid4()}",
        )
    )
    return adapter.acknowledge_interaction.await_args.kwargs


def _adapter() -> AsyncMock:
    """A platform whose every verb succeeds and is recorded."""
    return AsyncMock()


async def test_approve_on_a_card_cancelled_by_a_new_message_is_not_done() -> None:
    """The P0: the pause was auto-denied when they typed something else."""
    ack = await _tapped(stands=DENY)

    assert ack["text"] != "Done"
    assert "already cancelled" in ack["text"]
    assert ack["clear_actions"] is True, "the dead card kept live buttons"
    assert ack["show_alert"] is True


async def test_approve_handed_to_a_worker_is_not_reported_done_yet() -> None:
    ack = await _tapped(stands=APPROVE, status="queued")

    assert ack["text"] == "Approved — running it now."
    assert ack["clear_actions"] is True


async def test_a_tap_naming_a_conversation_said_nowhere_resolves_nothing() -> None:
    """The button's conversation id is unsigned. One with no place on this
    surface is not provably the tapper's, and there is nobody it would be safe
    to tell -- so nothing is resolved, and nothing is said."""
    surface = _slack_surface()
    parsed_event = _slack_event()
    current = _answered_in_thread(surface, uuid4(), parsed_event)
    adapter = _adapter()
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], answered=current
    )

    await service.handle_interaction(
        ParsedSurfaceInteraction(
            platform=SurfacePlatform.SLACK,
            external_channel_id=parsed_event.external_channel_id,
            external_thread_id=parsed_event.external_thread_id,
            external_user_id=current.external_user_id,
            callback_id=f"{uuid4()}|call-1",
            approval_decision="APPROVE_ONCE",
            dedup_id="tap-elsewhere",
        ),
        authorized_surface_ids=[surface.id],
    )

    agent_conversations.resolve_pending_interaction.assert_not_awaited()
    adapter.acknowledge_interaction.assert_not_awaited()


async def test_an_unreadable_tap_asks_for_another_and_keeps_the_buttons() -> None:
    """A Redis blip is not an expired button: the same tap may work in a moment."""
    surface = _telegram_surface()
    adapter = _adapter()
    adapter.parse_inbound_interaction.return_value = ParsedSurfaceInteraction(
        platform=SurfacePlatform.TELEGRAM, interaction_state="unreadable"
    )
    service = build_ingress_service(adapter=adapter, surfaces=[surface])

    handled = await service.try_handle_interaction(
        SurfaceDirectWebhookIngress(surface_id=surface.id, payload={}, headers={})
    )

    assert handled is True
    ack = adapter.acknowledge_interaction.await_args.kwargs
    assert "expired" not in ack["text"]
    assert "try again" in ack["text"].lower()
    assert ack["clear_actions"] is False
    agent_conversations.resolve_pending_interaction.assert_not_awaited()
