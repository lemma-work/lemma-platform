"""A stranger in a Telegram group, answered for the pod -- end to end.

The whole path, with only the model's next move scripted: a member adds the bot
to a group (Telegram's `my_chat_member`), which makes the pod adopt the group
and makes them the one who answers for it. Then somebody who is in no pod at
all @mentions the bot. They are answered, in the group, by a run that belongs
to the member's conversation for that group's outsiders -- and that run, asked
to list the pod's tables, sees only the one the pod marked Public.

The negative half of the promise already lives beside the other Telegram tests:
a stranger in a group *nobody* brought the bot into is still ignored.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.outsiders import AUDIENCE_KEY, OUTSIDERS
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent_surfaces.composition import build_app_event_handler
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _messages_for_conversation,
    _seed_external_user,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
    script_tool_call,
)

pytestmark = pytest.mark.e2e

GROUP_CHAT = -1009876543210
MEMBER_TELEGRAM_ID = 900100
STRANGER_TELEGRAM_ID = 777000


def _wire_native_telegram(monkeypatch, fake_telegram) -> None:
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )


def _bot_added(*, by: int) -> dict:
    return {
        "update_id": 5001,
        "my_chat_member": {
            "chat": {"id": GROUP_CHAT, "type": "supergroup", "title": "Launch crew"},
            "from": {"id": by, "is_bot": False, "first_name": "Arjun"},
            "date": 1700000000,
            "old_chat_member": {"status": "left", "user": {"id": 1, "is_bot": True}},
            "new_chat_member": {"status": "member", "user": {"id": 1, "is_bot": True}},
        },
    }


def _group_message(*, text: str, message_id: int, sender_id: int) -> dict:
    return {
        "update_id": 6000 + message_id,
        "message": {
            "message_id": message_id,
            "from": {"id": sender_id, "is_bot": False, "first_name": "Tom"},
            "chat": {"id": GROUP_CHAT, "type": "supergroup", "title": "Launch crew"},
            "date": 1700000100,
            "text": text,
            # What Telegram delivers to a privacy-mode bot for "@lemmabot ...".
            "entities": [{"type": "mention", "offset": 0, "length": 9}],
        },
    }


async def _table(client: AsyncClient, pod_id: str, *, name: str, visibility: str):
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": False,
            "visibility": visibility,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == 201, response.text


async def _adopt_group(db_session: AsyncSession, surface_id: UUID):
    handled = await build_app_event_handler(
        SqlAlchemyUnitOfWork(db_session)
    ).try_handle_lifecycle(
        SurfacePlatformWebhookIngress(
            source="telegram", payload=_bot_added(by=MEMBER_TELEGRAM_ID), headers={}
        )
    )
    await db_session.commit()
    assert handled is True
    return await SurfaceGroupRepository(db_session).get(
        surface_id=surface_id, external_channel_id=str(GROUP_CHAT)
    )


async def test_a_member_bringing_the_bot_in_answers_for_the_group(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    monkeypatch,
):
    _wire_native_telegram(monkeypatch, fake_telegram)
    surface = await _create_surface(
        authenticated_client, test_pod["id"], config={"type": "TELEGRAM"}
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(MEMBER_TELEGRAM_ID),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    group = await _adopt_group(db_session, UUID(surface["id"]))

    assert group is not None
    assert group.title == "Launch crew"
    assert group.owner_user_id == UUID(fixed_test_user["id"])
    assert group.welcomes_outsiders


async def test_a_stranger_is_answered_for_the_pod_from_what_is_public(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "TELEGRAM"}
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(MEMBER_TELEGRAM_ID),
        resolved_user_id=owner,
    )
    group = await _adopt_group(db_session, UUID(surface["id"]))
    assert group is not None
    await _table(authenticated_client, pod_id, name="customers", visibility="POD")
    await _table(authenticated_client, pod_id, name="price_list", visibility="PUBLIC")

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_group_message(
                text="@lemmabot what can you tell me?",
                message_id=81,
                sender_id=STRANGER_TELEGRAM_ID,
            ),
            headers={},
        ),
        script=[
            script_tool_call("pod_tables", {}, tool_call_id="tables-1"),
            script_text("I can share the price list."),
        ],
    )

    # Answered, and answered as the pod: the member's conversation for this
    # group's outsiders, never the stranger's own.
    assert isinstance(context, SurfaceChatContext)
    assert context.audience.answers_outsiders is True
    assert context.user_id == owner
    conversation = (
        await db_session.execute(
            select(ConversationModel).where(
                ConversationModel.id == context.conversation_id
            )
        )
    ).scalar_one()
    assert conversation.user_id == owner
    assert (conversation.conversation_metadata or {}).get(AUDIENCE_KEY) == OUTSIDERS

    # In the group, where the question was asked.
    sent = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert sent[-1]["chat_id"] == str(GROUP_CHAT)
    assert "price list" in sent[-1]["text"]

    # The run saw what the pod marked Public, and nothing it did not.
    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    listed = [
        json.dumps(message["tool_result"])
        for message in messages
        if message.get("tool_name") == "pod_tables" and message.get("tool_result")
    ]
    assert listed, "the scripted pod_tables call left no result"
    assert "price_list" in listed[-1]
    assert "customers" not in listed[-1]

    # The pod's log of the group holds both sides.
    lines = await SurfaceGroupRepository(db_session).recent_lines(group=group, limit=10)
    said = [line.text for line in lines]
    assert "@lemmabot what can you tell me?" in said
    assert "I can share the price list." in said
    assert any(line.from_agent for line in lines)


async def test_a_reply_in_a_logged_group_still_says_what_it_replies_to(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    monkeypatch,
):
    """The group's log is background; the message replied to is the subject.

    Telegram delivers the replied-to message inline, and the parser keeps it on
    the message itself -- so it reaches the run however much of the group the
    pod has logged.
    """
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "TELEGRAM"}
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(MEMBER_TELEGRAM_ID),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    group = await _adopt_group(db_session, UUID(surface["id"]))
    assert group is not None
    await SurfaceGroupRepository(db_session).append_line(
        group_id=group.id,
        body="Proofs are due Friday.",
        external_message_id="80",
        author_external_id=str(MEMBER_TELEGRAM_ID),
        author_name="Arjun",
    )
    await db_session.commit()
    payload = _group_message(
        text="@lemmabot is that still right?",
        message_id=82,
        sender_id=STRANGER_TELEGRAM_ID,
    )
    payload["message"]["reply_to_message"] = {
        "message_id": 80,
        "from": {"id": MEMBER_TELEGRAM_ID, "is_bot": False, "first_name": "Arjun"},
        "chat": {"id": GROUP_CHAT, "type": "supergroup", "title": "Launch crew"},
        "date": 1700000050,
        "text": "Proofs are due Friday.",
    }

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[script_text("Yes, still Friday.")],
    )

    assert isinstance(context, SurfaceChatContext)
    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    asked = [message for message in messages if message.get("role") == "user"][-1]
    assert asked["metadata"]["quoted_message"]["text"] == "Proofs are due Friday."
