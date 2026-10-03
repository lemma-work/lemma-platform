"""Saying where a message went, when the chat could not take it.

A closed WhatsApp window reroutes to email. Every caller that reports a send --
the agent's own tool, ``surface.send`` -- has to say "by email", or the agent
tells the person to check a chat that holds nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.infrastructure.events import inbox
from app.modules.agent_surfaces.domain.delivery_result import SurfaceDeliveryResult
from app.modules.agent_surfaces.platforms import surface_send_tools
from app.modules.agent_surfaces.services import turn_starter

pytestmark = pytest.mark.asyncio


async def _send_with(result) -> surface_send_tools.SurfaceSendMessageResult:
    async def deliver(*, conversation_id, message):
        return result

    tool = (
        surface_send_tools.build_surface_send_toolset(deliver=deliver)
        .tools["surface_send_message"]
        .function
    )
    return await tool(
        SimpleNamespace(
            deps=SimpleNamespace(conversation_id=uuid4(), delivers_to_surface=True)
        ),
        "the report is ready",
    )


async def test_the_send_tool_says_it_went_by_email():
    response = await _send_with(SurfaceDeliveryResult.by_email_because("closed"))

    assert response.success is True
    assert "Delivered by email" in (response.message or "")


async def test_the_send_tool_passes_on_why_nothing_went():
    response = await _send_with(SurfaceDeliveryResult.undelivered("window closed"))

    assert response.success is False
    assert response.message == "window closed"


async def test_a_chat_delivery_needs_no_explanation():
    response = await _send_with(SurfaceDeliveryResult.on_chat())

    assert response.success is True
    assert response.message is None


async def test_a_result_reads_as_the_bool_it_replaced():
    assert SurfaceDeliveryResult.on_chat()
    assert SurfaceDeliveryResult.by_email_because("closed")
    assert not SurfaceDeliveryResult.undelivered()


async def test_the_inbox_says_which_attempt_is_running():
    seen: list[inbox.InboxAttempt | None] = []

    async def handler() -> None:
        seen.append(inbox.inbox_attempt())

    consumer = inbox.InboxConsumer(AsyncMock())
    consumer._claim = AsyncMock(return_value=3)
    consumer._finish = AsyncMock()

    await consumer.process(
        "test.consumer", {"event_id": str(uuid4())}, handler, max_attempts=3
    )

    assert seen == [inbox.InboxAttempt(number=3, max_attempts=3)]
    assert seen[0].is_final
    assert inbox.inbox_attempt() is None


async def test_a_quote_of_the_bots_message_is_filled_from_what_was_sent():
    quoted = {"author": None, "text": "Your table is ready.", "is_bot": True}
    lookup = AsyncMock(return_value=quoted)

    class _Uows:
        async def __aenter__(self):
            return SimpleNamespace(session=None)

        async def __aexit__(self, *_exc):
            return False

    starter = turn_starter.SurfaceTurnStarter(uow_factory=_Uows, quote_lookup=lookup)
    metadata = {"reply_ref": {"id": "wamid.out-1", "from": "1555", "is_bot": True}}
    context = SimpleNamespace(platform=SimpleNamespace(value="WHATSAPP"))

    await starter._resolve_quote(context, metadata)

    assert metadata["quoted_message"] == quoted
    assert lookup.await_args.kwargs["external_message_id"] == "wamid.out-1"
