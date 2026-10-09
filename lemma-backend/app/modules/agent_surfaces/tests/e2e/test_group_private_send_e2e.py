"""A word for one person in a group is never posted in the group -- end to end.

`surface_send_message` exists so a run can reach the person it is working for
mid-task rather than waiting for its final reply, and it says the message goes
to that person alone. In a group the conversation is one member's but its
address is the group, so delivering to the conversation posted the aside in
front of everybody in it.

What happens instead: it goes to that member's own chat with the bot, which is
the only thread a message meant for one person is ever answered to. A member
whose only thread is the group has nowhere private to receive it, and then it is
not sent at all -- the group is not a fallback.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _messages_for_conversation,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.test_outsider_isolation_e2e import (
    _group_with_owner,
)
from app.modules.agent_surfaces.tests.e2e.test_telegram_group_outsiders_e2e import (
    GROUP_CHAT,
    MEMBER_TELEGRAM_ID,
    _group_message,
)

pytestmark = pytest.mark.e2e

ASIDE = "ASIDE-FOR-YOU-ONLY"
ANSWER = "ANSWER-FOR-THE-GROUP"


async def _allow_send(db_session: AsyncSession, surface_id: str) -> None:
    """Switch the surface's ``surface_send_message`` tool on for these runs."""
    surface = await db_session.get(AgentSurface, UUID(surface_id))
    assert surface is not None
    surface.config = {**(surface.config or {}), "send_policy": {"allow_send": True}}
    await db_session.commit()


async def _member_writes_privately(db_session: AsyncSession) -> None:
    """The member's own chat with the bot, which is where an aside can land."""
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text="hello", message_id=1, sender_id=MEMBER_TELEGRAM_ID
            ),
            headers={},
        ),
        script=[script_text("Hello.")],
    )
    assert isinstance(context, SurfaceChatContext)


async def _member_asks_in_the_group(db_session: AsyncSession) -> SurfaceChatContext:
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_group_message(
                text="@lemmabot what did we agree?",
                message_id=403,
                sender_id=MEMBER_TELEGRAM_ID,
            ),
            headers={},
        ),
        script=[
            script_tool_call(
                "surface_send_message", {"message": ASIDE}, tool_call_id="aside"
            ),
            script_text(ANSWER),
        ],
    )
    assert isinstance(context, SurfaceChatContext)
    return context


def _to(messages: list[dict], chat_id: int) -> list[str]:
    return [
        message.get("text") or ""
        for message in messages
        if str(message.get("chat_id")) == str(chat_id)
    ]


async def _tool_result(scenario, conversation_id: UUID, tool_call_id: str) -> dict:
    messages = await _messages_for_conversation(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=str(conversation_id),
    )
    raw = next(
        message["tool_result"]
        for message in messages
        if message.get("tool_call_id") == tool_call_id
    )
    return raw if isinstance(raw, dict) else json.loads(raw)


async def test_a_mid_task_message_from_a_group_reaches_the_member_alone(
    scenario, db_session: AsyncSession, fake_telegram, message_store, monkeypatch
):
    surface, _group, _owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    await _allow_send(db_session, surface["id"])
    await _member_writes_privately(db_session)

    context = await _member_asks_in_the_group(db_session)
    sent = await wait_for_messages(
        message_store,
        "TELEGRAM",
        predicate=lambda message: ANSWER in (message.get("text") or ""),
    )

    assert ASIDE in _to(sent, MEMBER_TELEGRAM_ID)
    assert ASIDE not in _to(sent, GROUP_CHAT)
    assert ANSWER in _to(sent, GROUP_CHAT)

    result = await _tool_result(scenario, context.conversation_id, "aside")
    assert result["success"] is True


async def test_a_member_with_no_private_chat_is_not_reached_in_the_group(
    scenario, db_session: AsyncSession, fake_telegram, message_store, monkeypatch
):
    surface, _group, _owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    await _allow_send(db_session, surface["id"])

    context = await _member_asks_in_the_group(db_session)
    sent = await wait_for_messages(
        message_store,
        "TELEGRAM",
        predicate=lambda message: ANSWER in (message.get("text") or ""),
    )

    assert ASIDE not in _to(sent, GROUP_CHAT)
    assert ASIDE not in _to(sent, MEMBER_TELEGRAM_ID)
    assert ANSWER in _to(sent, GROUP_CHAT)

    result = await _tool_result(scenario, context.conversation_id, "aside")
    assert result["success"] is False
    assert "group chat" in result["message"]
