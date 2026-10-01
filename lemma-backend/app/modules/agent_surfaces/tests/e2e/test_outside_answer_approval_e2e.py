"""A question from outside the pod, passed to a member, answered only as they approve.

The whole round trip on Telegram, with only the model's moves scripted:

1. A stranger in a group the member brought the bot into asks something the
   pod has not made Public. The stranger's run passes it on with
   ``message_user`` -- which reaches the member, in their own chat with the bot,
   framed by the server: which group, who asked, their words quoted.
2. The member answers. Their agent -- acting with all of their access -- drafts
   the answer with a private value in it, and is told it cannot send it: the
   answer goes to a stranger, so the member approves the exact words first.
3. Until they do, nothing reaches the group and the question stays open. The
   card they approve is the server's: it names the group, shows every word, and
   offers no "approve for the session". A stranger pressing Approve is refused.
4. The member approves; exactly those words are recorded, and the stranger's
   run relays them into the group.

And the doors around it: a decline leaves the question open with nothing sent,
and an agent holding the member's delegated token cannot answer it over the API.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.outsiders import OUTSIDE_ANSWER_TOOL
from app.modules.agent.services.message_reply_service import MessageReplyService
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import NotificationModel
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    telegram as telegram_payloads,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    resume_latest_scripted_run,
    run_scripted_agent_run,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import _telegram_control
from app.modules.agent_surfaces.tests.e2e.test_outsider_isolation_e2e import (
    _group_with_owner,
    _stranger_asks,
)
from app.modules.agent_surfaces.tests.e2e.test_telegram_group_outsiders_e2e import (
    GROUP_CHAT,
    MEMBER_TELEGRAM_ID,
    STRANGER_TELEGRAM_ID,
)

pytestmark = pytest.mark.e2e

PRIVATE_VALUE = "PRIVATE-4711"
ANSWER = f"For 500 units the rate is 42k ({PRIVATE_VALUE})."
APPROVAL_CALL = "relay-approval-1"


async def _owner_says(db_session: AsyncSession, *, text: str, message_id: int, script):
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=telegram_payloads.dm(
                text=text, message_id=message_id, sender_id=MEMBER_TELEGRAM_ID
            ),
            headers={},
        ),
        script=script,
    )
    assert isinstance(context, SurfaceChatContext)
    assert context.answers_outsider is False
    return context


async def _passed_on_question(scenario, db_session, fake_telegram, monkeypatch):
    """The member has a chat with the bot; a stranger's question reaches it."""
    _, group, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    # The member has talked to the bot privately, so it can reach them there.
    await _owner_says(
        db_session, text="hi", message_id=301, script=[script_text("Hello!")]
    )
    stranger = await _stranger_asks(
        db_session,
        text="what's your rate for 500 units?",
        message_id=302,
        script=[
            script_tool_call(
                "message_user",
                {"to": "anyone", "message": "Dana asks: the rate for 500 units?"},
                tool_call_id="pass-on",
            ),
            script_text("I've asked the team; I'll be back with an answer."),
        ],
    )
    asked = (
        await db_session.execute(
            select(NotificationModel).where(
                NotificationModel.origin_conversation_id == stranger.conversation_id
            )
        )
    ).scalar_one()
    return group, owner, stranger, asked


def _sent_to(message_store, chat_id: int) -> list[dict]:
    return [
        message
        for message in message_store.get_all("TELEGRAM")
        if str(message.get("chat_id")) == str(chat_id)
    ]


async def _drafts_and_asks_approval(db_session, notification_id) -> SurfaceChatContext:
    """The member answers; their agent drafts, is refused, and asks approval."""
    return await _owner_says(
        db_session,
        text=f"Tell them 42k -- and our code is {PRIVATE_VALUE}",
        message_id=303,
        script=[
            script_tool_call(
                OUTSIDE_ANSWER_TOOL,
                {"notification_id": str(notification_id), "summary": ANSWER},
                tool_call_id="draft-1",
            ),
            script_tool_call(
                "request_approval",
                {
                    "tool_name": OUTSIDE_ANSWER_TOOL,
                    "args": {
                        "notification_id": str(notification_id),
                        "summary": ANSWER,
                    },
                    "title": "Just logging the call",
                    "reason": "routine",
                },
                tool_call_id=APPROVAL_CALL,
            ),
            script_text("Sent."),
        ],
    )


async def _press(
    db_session: AsyncSession, *, token: str, sender_id: int, update_id: int
):
    uow = SqlAlchemyUnitOfWork(db_session)
    handled = await build_surface_ingress(uow).try_handle_interaction(
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=telegram_payloads.button_press(
                token=token, sender_id=sender_id, update_id=update_id
            ),
            headers={},
        )
    )
    await uow.commit()
    return handled


async def test_an_outside_answer_reaches_the_group_only_after_the_owner_approves_it(
    scenario, db_session: AsyncSession, fake_telegram, message_store, monkeypatch
):
    group, owner, stranger, asked = await _passed_on_question(
        scenario, db_session, fake_telegram, monkeypatch
    )

    # Marked by the server, from the stranger's conversation -- not by the run.
    assert asked.from_outside is True
    assert asked.origin_group_title == "Launch crew"
    assert asked.recipient_user_id == owner
    # The member reads it framed and quoted, naming the group.
    to_member = _sent_to(message_store, MEMBER_TELEGRAM_ID)
    framed = next(m["text"] for m in to_member if "outside the pod" in m["text"])
    assert "“Launch crew”" in framed
    assert "> Dana asks: the rate for 500 units?" in framed

    owner_turn = await _drafts_and_asks_approval(db_session, asked.id)

    # Drafted, but nothing is recorded or sent yet.
    await db_session.refresh(asked)
    assert asked.status == "OPEN"
    assert asked.response_summary is None
    assert not any(
        PRIVATE_VALUE in (m.get("text") or "")
        for m in _sent_to(message_store, GROUP_CHAT)
    )

    # The card is the server's: the group, every word, no session approval.
    card_messages = _sent_to(message_store, MEMBER_TELEGRAM_ID)
    rendered = json.dumps(card_messages, ensure_ascii=False)
    assert "Send this answer to “Launch crew”?" in rendered
    assert ANSWER in rendered
    assert "Just logging the call" not in rendered
    assert _telegram_control(card_messages, "Approve") is not None
    assert not any(
        "session" in json.dumps(m.get("reply_markup") or {}).lower()
        for m in card_messages
    )
    approve = _telegram_control(card_messages, "Approve")

    # Somebody else pressing it decides nothing.
    await _press(
        db_session, token=approve.value, sender_id=STRANGER_TELEGRAM_ID, update_id=7001
    )
    await db_session.refresh(asked)
    assert asked.status == "OPEN"

    # The member approves: exactly those words are recorded.
    assert await _press(
        db_session, token=approve.value, sender_id=MEMBER_TELEGRAM_ID, update_id=7002
    )
    await resume_latest_scripted_run(
        db_session,
        conversation_id=owner_turn.conversation_id,
        user_id=owner,
        pod_id=owner_turn.pod_id,
        agent_name=owner_turn.agent_name,
        approval_id=APPROVAL_CALL,
    )
    await db_session.refresh(asked)
    assert asked.status == "RESPONDED"
    assert asked.response_summary == ANSWER
    assert asked.response_data is None

    # The stranger's thread wakes, reads the approved words, and relays them.
    with suppress_agent_run_enqueue():
        assert await MessageReplyService(SqlAlchemyUnitOfWork(db_session)).deliver(
            conversation_id=stranger.conversation_id, pod_id=stranger.pod_id
        )
    await db_session.commit()
    await run_scripted_agent_run(
        db_session,
        conversation_id=stranger.conversation_id,
        user_id=owner,
        pod_id=stranger.pod_id,
        agent_name=stranger.agent_name,
        script=[
            script_tool_call(
                "check_messages",
                {"notification_ids": [str(asked.id)]},
                tool_call_id="read-answer",
            ),
            script_text(ANSWER),
        ],
    )
    relayed = await wait_for_messages(
        message_store,
        "TELEGRAM",
        min_count=1,
        predicate=lambda m: (
            str(m.get("chat_id")) == str(GROUP_CHAT) and ANSWER in (m.get("text") or "")
        ),
    )
    assert relayed


async def test_a_declined_outside_answer_leaves_the_question_open(
    scenario, db_session: AsyncSession, fake_telegram, message_store, monkeypatch
):
    _, owner, _stranger, asked = await _passed_on_question(
        scenario, db_session, fake_telegram, monkeypatch
    )
    owner_turn = await _drafts_and_asks_approval(db_session, asked.id)
    deny = _telegram_control(_sent_to(message_store, MEMBER_TELEGRAM_ID), "Deny")
    assert deny is not None

    assert await _press(
        db_session, token=deny.value, sender_id=MEMBER_TELEGRAM_ID, update_id=7101
    )
    await resume_latest_scripted_run(
        db_session,
        conversation_id=owner_turn.conversation_id,
        user_id=owner,
        pod_id=owner_turn.pod_id,
        agent_name=owner_turn.agent_name,
        approval_id=APPROVAL_CALL,
    )

    await db_session.refresh(asked)
    assert asked.status == "OPEN"
    assert asked.response_summary is None
    assert not any(
        PRIVATE_VALUE in (m.get("text") or "")
        for m in _sent_to(message_store, GROUP_CHAT)
    )


async def test_an_agent_cannot_answer_an_outside_question_through_the_api(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    """`lemma notifications respond` from the member's sandbox is not their say-so."""
    _, _, _, asked = await _passed_on_question(
        scenario, db_session, fake_telegram, monkeypatch
    )
    headers = await scenario.default_pod_agent_headers(user=scenario.owner_user)

    refused = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/notifications/{asked.id}/respond",
        json={"summary": ANSWER},
        headers=headers,
    )
    assert refused.status_code == 403, refused.text
    await db_session.refresh(asked)
    assert asked.status == "OPEN"

    # The member typing it in the app is their say-so.
    typed = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/notifications/{asked.id}/respond",
        json={"summary": "42k for 500 units."},
    )
    assert typed.status_code == 200, typed.text
    await db_session.refresh(asked)
    assert asked.status == "RESPONDED"
    assert asked.response_summary == "42k for 500 units."
    assert UUID(typed.json()["id"]) == asked.id
