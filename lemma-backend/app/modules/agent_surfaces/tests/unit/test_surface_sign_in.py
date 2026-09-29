"""The sign-in message a surface delivers: the agent's words, the surface's safety."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.core.config import settings
from app.modules.agent_surfaces.services.surface_sign_in import sign_in_prompt_envelope

_OPERATIONS = "app.modules.agent.contracts.conversations_for_surfaces"


async def _envelope(tool_args: dict, *, narration: str | None = None):
    conversation_id = uuid4()
    waiting = SimpleNamespace(tool_call_id="call-1", tool_args=tool_args)
    with (
        patch(f"{_OPERATIONS}.surface_conversation", AsyncMock(return_value=object())),
        patch(f"{_OPERATIONS}.pending_sign_in", AsyncMock(return_value=waiting)),
    ):
        envelope = await sign_in_prompt_envelope(
            object(),
            conversation_id=conversation_id,
            tool_call_id="call-1",
            narration=narration,
        )
    return conversation_id, envelope


async def test_the_envelope_carries_the_agents_message_and_the_link() -> None:
    conversation_id, envelope = await _envelope(
        {"origin": "https://bank.example", "reason": "get the statement"},
        narration="One moment.",
    )

    link = (
        f"{settings.frontend_url.rstrip('/')}/sign-in-to-site/{conversation_id}/call-1"
    )
    assert envelope.text.startswith("One moment.\n\n")
    assert "I need you to sign in to https://bank.example so I can carry on." in (
        envelope.text
    )
    assert "What I am doing: get the statement" in envelope.text
    assert link in envelope.text


async def test_the_reason_is_made_safe_for_the_surface_before_it_is_shown() -> None:
    _, envelope = await _envelope(
        {"origin": "https://x.example", "reason": "<think>hidden</think>log in"}
    )
    assert "hidden" not in envelope.text
    assert "What I am doing: log in" in envelope.text


async def test_a_pause_without_a_site_sends_nothing() -> None:
    _, envelope = await _envelope({"reason": "why"})
    assert envelope is None
