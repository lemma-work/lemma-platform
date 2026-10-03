"""A button that fails must say so, be logged, and be tappable again.

`handle_interaction` claimed the replay key first, before authorization and
before the answer reached the run, and its `except Exception` acknowledged the
tap and logged nothing. A failure therefore left the claim spent -- the person's
second tap was ignored as a replay, in silence -- and left no trace for anybody
to find. On Slack's Socket Mode the same taps never arrived at all: every
envelope but an Events API event was dropped, and the ones that were kept were
acknowledged before they were published.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
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
from app.modules.agent_surfaces.services.event_receiver_service import (
    NativeReceiverCandidate,
    SlackSocketReceiverRunner,
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


async def _tap(*, sender: str | None = None, decision: str = "APPROVE_ONCE"):
    """A service, and an approval tap from the person the thread belongs to."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.surface_conversation.return_value = _surface_conversation(
        surface, conversation_id=conversation_id
    )
    # Still open: a tap on a closed question is answered as stale and goes no
    # further, which is not the failure these tests are about.
    agent_conversations.pending_interaction.return_value = _pending(
        "request_approval", tool_call_id="tool-1"
    )
    interaction = ParsedSurfaceInteraction(
        platform=SurfacePlatform.SLACK,
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=sender or link.external_user_id,
        callback_id=f"{conversation_id}|tool-1",
        approval_decision=decision,
        dedup_id="tap-1",
    )
    return service, adapter, interaction, surface


async def test_a_failure_is_logged_acknowledged_and_leaves_the_tap_repeatable(caplog):
    service, adapter, interaction, surface = await _tap()
    agent_conversations.resolve_pending_interaction.side_effect = RuntimeError(
        "resume exploded"
    )

    with caplog.at_level(logging.DEBUG):
        await service.handle_interaction(interaction)  # never raises

    failures = [r for r in caplog.records if "surface_interaction_failed" in r.message]
    assert failures, [r.message for r in caplog.records]
    assert failures[0].levelno >= logging.WARNING
    assert "resume exploded" in caplog.text, "the traceback must reach the log"

    adapter.acknowledge_interaction.assert_awaited_once()
    assert (
        "couldn’t complete" in adapter.acknowledge_interaction.await_args.kwargs["text"]
    )
    claimed = service.event_dedup_store.claim_message.await_args.kwargs
    service.event_dedup_store.release_message.assert_awaited_once()
    assert service.event_dedup_store.release_message.await_args.kwargs == claimed


async def test_the_claim_is_kept_once_the_answer_has_reached_the_run(caplog):
    """A failed *acknowledgement* is not a failed action: do not let it repeat."""
    service, adapter, interaction, _ = await _tap()
    adapter.acknowledge_interaction.side_effect = RuntimeError("slack is down")

    with caplog.at_level(logging.DEBUG):
        await service.handle_interaction(interaction)

    agent_conversations.resolve_pending_interaction.assert_awaited_once()
    service.event_dedup_store.release_message.assert_not_awaited()
    assert "surface_interaction_failed" in caplog.text
    # And it does not tell them it failed, when it did not.
    assert adapter.acknowledge_interaction.await_count == 1


async def test_a_failure_to_release_or_acknowledge_never_escapes(caplog):
    service, adapter, interaction, _ = await _tap()
    agent_conversations.resolve_pending_interaction.side_effect = RuntimeError("x")
    service.event_dedup_store.release_message.side_effect = ConnectionError("redis")
    adapter.acknowledge_interaction.side_effect = ConnectionError("slack")

    with caplog.at_level(logging.DEBUG):
        await service.handle_interaction(interaction)

    assert "surface_interaction_claim_release_failed" in caplog.text
    assert "surface_interaction_failure_unacknowledged" in caplog.text


async def test_a_refused_submitter_does_not_spend_the_replay_claim():
    service, adapter, interaction, _ = await _tap(sender="U-mallory")

    await service.handle_interaction(interaction)

    service.event_dedup_store.claim_message.assert_not_awaited()
    adapter.acknowledge_interaction.assert_awaited_once()
    agent_conversations.resolve_pending_interaction.assert_not_awaited()


async def test_a_gone_conversation_on_retry_is_acknowledged():
    service, adapter, interaction, _ = await _tap()
    interaction = interaction.model_copy(update={"action": "retry"})
    service._refresh_interaction_conversation = AsyncMock(return_value=None)

    await service.handle_interaction(interaction)

    adapter.acknowledge_interaction.assert_awaited_once()
    assert "gone" in adapter.acknowledge_interaction.await_args.kwargs["text"]


# --- Slack Socket Mode ---------------------------------------------------


def _runner() -> SlackSocketReceiverRunner:
    return SlackSocketReceiverRunner(
        NativeReceiverCandidate(
            key="slack:system:abc",
            platform=SurfacePlatform.SLACK,
            surface_ids=(uuid4(),),
            credential_label="system",
            credentials={"app_token": "xapp-1"},
        )
    )


def _envelope(kind: str) -> SimpleNamespace:
    return SimpleNamespace(
        type=kind, envelope_id="env-1", payload={"type": "block_actions"}
    )


_PUBLISH = "app.core.infrastructure.events.publisher.EventPublisher.publish"


@pytest.mark.parametrize("kind", ["events_api", "interactive"])
async def test_socket_mode_publishes_events_and_interactions_then_acknowledges(
    monkeypatch, kind
):
    order: list[str] = []
    publish = AsyncMock(side_effect=lambda *_: order.append("publish"))
    monkeypatch.setattr(_PUBLISH, publish)
    socket_client = SimpleNamespace(
        send_socket_mode_response=AsyncMock(side_effect=lambda *_: order.append("ack"))
    )

    await _runner()._handle_envelope(socket_client, _envelope(kind))

    assert order == ["publish", "ack"]
    event = publish.await_args.args[1]
    assert event.source == "slack" and event.payload == {"type": "block_actions"}


async def test_socket_mode_leaves_an_unpublished_envelope_unacknowledged(
    monkeypatch, caplog
):
    """Acknowledged first, a publish failure lost the event; Slack cannot redeliver
    what it was told arrived."""
    monkeypatch.setattr(_PUBLISH, AsyncMock(side_effect=ConnectionError("redis blip")))
    socket_client = SimpleNamespace(send_socket_mode_response=AsyncMock())

    with caplog.at_level(logging.DEBUG):
        # Left to Slack's client, which logs it and leaves the envelope unacked.
        with pytest.raises(ConnectionError):
            await _runner()._handle_envelope(socket_client, _envelope("interactive"))

    socket_client.send_socket_mode_response.assert_not_awaited()
    assert "slack_socket_publish_failed" in caplog.text
    assert "redis blip" in caplog.text


async def test_socket_mode_acknowledges_an_envelope_it_cannot_use(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(_PUBLISH, publish)
    socket_client = SimpleNamespace(send_socket_mode_response=AsyncMock())

    await _runner()._handle_envelope(socket_client, _envelope("slash_commands"))

    publish.assert_not_awaited()
    socket_client.send_socket_mode_response.assert_awaited_once()
