"""E2E for what holds a push back on its way to a member, and what never is.

Two things hold one: having read its lines in the app (it is not pushed), and
having the app open (it waits a few minutes, and goes then if still unread,
with whatever came meanwhile). A busy channel is one push a window, however
many lines it hears. What names them is never held; an agent's paused question
is held like any line -- its chat shows it too -- and not pushed at all once
answered there. A
plain line in a group is not worth an email. Against a real database, real
Redis and the fake mail server.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.live.presence import mark_online
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    member_writes,
)
from app.modules.agent_surfaces.services.push_sends import (
    ANSWERED_FIRST,
    ASK_HELD_WHILE_IN_APP,
)
from app.modules.agent_surfaces.tests.e2e.test_channel_deliveries_e2e import (
    _colleague,
    _deliver,
    _my_channel,
    _resend_bot,
    _row,
    _tell,
)
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.channels.contracts import (
    Asker,
    DeliveryReasonCode,
    DeliveryStatus,
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    QuestionState,
    Sender,
    post,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.models import ChannelMemberModel
from app.modules.channels.tests.e2e.push_support import (
    run_pushes,
    waiting,
    window_ends_now,
)
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _said_minutes_ago(message_ids: list[str], minutes: int) -> None:
    """These lines are older than the app-open hold by now."""
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMessageModel)
            .where(ChannelMessageModel.id.in_([UUID(each) for each in message_ids]))
            .values(
                created_at=ChannelMessageModel.created_at - timedelta(minutes=minutes)
            )
        )
        await session.commit()


async def _a_group_with(
    client: AsyncClient, pod_id: str, colleague: dict, *, notify: str = "ALL"
) -> str:
    created = await client.post(
        f"/pods/{pod_id}/channels",
        json={"title": "Launch", "members": [colleague["id"]]},
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == UUID(channel_id),
                ChannelMemberModel.user_id == UUID(colleague["id"]),
            )
            .values(notify=notify)
        )
        await session.commit()
    return channel_id


async def _agent_asks(pod_id: str, channel_id: str, user_id: str, text: str) -> str:
    """An agent's chat with the member pauses on ``ask_user``: the question
    is a line of the member's own channel, as ``pause_questions`` posts it."""
    member = Principal.member(UUID(user_id))
    async with async_session_maker() as session:
        chat = ConversationModel(user_id=UUID(user_id), pod_id=UUID(pod_id))
        session.add(chat)
        await session.commit()
        chat_id = chat.id
    async with create_uow_from_session_maker(async_session_maker) as uow:
        asked = await post(
            uow,
            channel_id=UUID(channel_id),
            line=NewLine(
                sender=Sender.agent(UUID(pod_id), on_behalf_of=member),
                text=text,
                conversation_id=chat_id,
            ),
            question=NewQuestion(
                kind=QuestionKind.ASK,
                ref=f"tool:{chat_id}:call_1",
                asker=Asker.tool(chat_id, "call_1"),
                question={"tool": "ask_user", "prompt": text},
                awaits=member,
            ),
        )
        await uow.commit()
    assert asked is not None
    return str(asked.id)


async def _answered_in_its_chat(line_id: str, user_id: str) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await channel_questions.close(
            uow,
            message_id=UUID(line_id),
            state=QuestionState.ANSWERED,
            answer={"answers": {"Repositories": ["one"]}},
            by=Principal.member(UUID(user_id)),
        )
        await uow.commit()


async def _pushes_to(line_id: str, user_id: str) -> list:
    return [
        delivery
        for delivery in await deliveries_of_line(line_id)
        if delivery.recipient_user_id == UUID(user_id)
    ]


async def _push_to(line_id: str, user_id: str):
    [push] = await _pushes_to(line_id, user_id)
    return push


@pytest.fixture
async def setting(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
):
    """A pod with a mailbox, and a colleague in it to be pushed to."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )
    colleague = await _colleague(
        authenticated_client, async_client, org_id=fixed_test_org["id"], pod_id=pod_id
    )
    return pod_id, colleague


async def test_a_line_naming_them_in_a_group_is_emailed(
    authenticated_client, setting, message_store
):
    """A plain group line waits in Lemma; one that names them goes, by email
    if nothing else reaches them."""
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)

    named = await member_writes(
        authenticated_client,
        pod_id,
        group,
        "Can you take the release notes?",
        parts=[{"type": "mention", "kind": "MEMBER", "id": colleague["id"]}],
    )
    push = await _push_to(named["id"], colleague["id"])
    await _deliver(push.id)

    assert (await _row(push.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert mail["to"] == [colleague["email"]]


async def test_a_plain_group_line_is_not_emailed(
    authenticated_client, setting, message_store
):
    """No chat app reaches them, and the line does not name them: it waits in
    Lemma rather than spending the pod's emails on a group's chatter."""
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)

    plain = await member_writes(authenticated_client, pod_id, group, "Lunch at one.")
    push = await _push_to(plain["id"], colleague["id"])
    await _deliver(push.id)

    skipped = await _row(push.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.reason == UndeliverableReason.NOT_WORTH_AN_EMAIL
    assert message_store.get_all("RESEND") == []


async def test_a_push_held_while_the_app_is_open_goes_with_what_came_meanwhile(
    authenticated_client, setting, message_store
):
    """Held while they had the app open, a push is not forgotten: the next
    line waits with it, and once both are old enough and still unread they go
    as one push that says the other is waiting."""
    pod_id, colleague = setting
    await mark_online(UUID(pod_id), UUID(colleague["id"]))
    first = await _tell(authenticated_client, pod_id, colleague["email"], text="One.")
    held = await _push_to(first["message_id"], colleague["id"])
    await _deliver(held.id)
    row = await _row(held.id)
    assert (row.status, row.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.FOLDED,
    )
    second = await _tell(authenticated_client, pod_id, colleague["email"], text="Two.")
    assert await _pushes_to(second["message_id"], colleague["id"]) == []
    assert await waiting(first["channel_id"], colleague["id"]) == 2
    assert message_store.get_all("RESEND") == []

    await _said_minutes_ago([first["message_id"], second["message_id"]], 10)
    await window_ends_now(first["channel_id"], colleague["id"])
    await run_pushes()
    together = await _push_to(second["message_id"], colleague["id"])
    await _deliver(together.id)

    assert (together.lines, together.from_seq) == (2, row.seq)
    assert (await _row(together.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "Two." in mail["text"] and "1 more line waiting in Lemma" in mail["text"]


async def test_a_line_naming_them_is_pushed_while_they_have_the_app_open(
    authenticated_client, setting, message_store
):
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)
    await mark_online(UUID(pod_id), UUID(colleague["id"]))

    named = await member_writes(
        authenticated_client,
        pod_id,
        group,
        "Your slides?",
        parts=[{"type": "mention", "kind": "MEMBER", "id": colleague["id"]}],
    )
    called = await _push_to(named["id"], colleague["id"])
    await _deliver(called.id)

    assert (await _row(called.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "Your slides?" in mail["text"]


async def test_having_the_app_open_is_not_reading_the_line(
    authenticated_client, setting, message_store
):
    """The app open somewhere in the pod holds a push a few minutes, not for
    good: still unread once its line is that old, it goes -- and the hold
    says so, rather than that they read it."""
    pod_id, colleague = setting
    await mark_online(UUID(pod_id), UUID(colleague["id"]))
    told = await _tell(authenticated_client, pod_id, colleague["email"], text="One.")
    held = await _push_to(told["message_id"], colleague["id"])
    await _deliver(held.id)

    row = await _row(held.id)
    assert (row.status, row.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.FOLDED,
    )
    assert "read" not in (row.reason or "").replace("unread", "")
    assert await waiting(told["channel_id"], colleague["id"]) == 1
    assert message_store.get_all("RESEND") == []

    await _said_minutes_ago([told["message_id"]], 10)
    await window_ends_now(told["channel_id"], colleague["id"])
    await run_pushes()
    # The same push, planned again once the hold ended.
    later = await _push_to(told["message_id"], colleague["id"])
    assert later.status is DeliveryStatus.PENDING
    await _deliver(later.id)

    assert (await _row(later.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "One." in mail["text"]


async def test_a_line_they_read_in_the_app_is_not_pushed(
    authenticated_client, async_client, setting, message_store
):
    pod_id, colleague = setting
    told = await _tell(authenticated_client, pod_id, colleague["email"], text="One.")
    push = await _push_to(told["message_id"], colleague["id"])
    read = await async_client.post(
        f"/pods/{pod_id}/channels/{told['channel_id']}/read",
        json={"seq": push.seq},
        headers=auth_headers(colleague),
    )
    assert read.status_code == 200, read.text

    await _deliver(push.id)

    skipped = await _row(push.id)
    assert (skipped.status, skipped.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.IN_APP,
    )
    assert skipped.reason == "They read it in the app before it was pushed."
    assert message_store.get_all("RESEND") == []


async def test_a_burst_is_one_push_a_window_and_none_once_read(
    authenticated_client, async_client, setting, message_store
):
    """Five lines in a minute are one push, then one more for the four that
    came inside its window -- and not that one either, once they have read
    them in the app."""
    pod_id, colleague = setting
    burst = [
        await _tell(authenticated_client, pod_id, colleague["email"], text=f"{n}.")
        for n in range(1, 6)
    ]
    first = await _push_to(burst[0]["message_id"], colleague["id"])
    await _deliver(first.id)
    assert (await _row(first.id)).status is DeliveryStatus.SENT
    for told in burst[1:]:
        assert await _pushes_to(told["message_id"], colleague["id"]) == []
    channel_id = burst[0]["channel_id"]
    assert await waiting(channel_id, colleague["id"]) == 4

    last = burst[-1]
    await window_ends_now(channel_id, colleague["id"])
    await run_pushes()
    rest = await _push_to(last["message_id"], colleague["id"])
    assert rest.lines == 4 and rest.from_seq > first.seq
    read = await async_client.post(
        f"/pods/{pod_id}/channels/{channel_id}/read",
        json={"seq": rest.seq},
        headers=auth_headers(colleague),
    )
    assert read.status_code == 200, read.text
    await _deliver(rest.id)

    skipped = await _row(rest.id)
    assert (skipped.status, skipped.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.IN_APP,
    )
    [mail] = message_store.get_all("RESEND")
    assert "1." in mail["text"]


async def test_a_question_answered_in_its_chat_meanwhile_is_never_pushed(
    authenticated_client, db_session, setting, message_store
):
    """The member is in the chat that asked: its question waits the few
    minutes any line would, and once they have answered it there, it is not
    emailed after them."""
    pod_id, colleague = setting
    channel_id = await _my_channel(db_session, pod_id, colleague["id"])
    await mark_online(UUID(pod_id), UUID(colleague["id"]))
    asked = await _agent_asks(pod_id, channel_id, colleague["id"], "Which repos?")
    held = await _push_to(asked, colleague["id"])
    assert held.urgent
    await _deliver(held.id)
    row = await _row(held.id)
    assert (row.status, row.reason_code, row.reason) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.FOLDED,
        ASK_HELD_WHILE_IN_APP,
    )

    await _answered_in_its_chat(asked, colleague["id"])
    await _said_minutes_ago([asked], 10)
    await window_ends_now(channel_id, colleague["id"])
    await run_pushes()
    later = await _push_to(asked, colleague["id"])
    await _deliver(later.id)

    skipped = await _row(later.id)
    assert (skipped.status, skipped.reason_code, skipped.reason) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.IN_APP,
        ANSWERED_FIRST,
    )
    assert message_store.get_all("RESEND") == []


async def test_a_question_left_unanswered_goes_once_the_hold_ends(
    authenticated_client, db_session, setting, message_store
):
    """Held while the app was open, a question still waiting is pushed
    when the hold ends -- as one that asks them, though it was folded back."""
    pod_id, colleague = setting
    channel_id = await _my_channel(db_session, pod_id, colleague["id"])
    await mark_online(UUID(pod_id), UUID(colleague["id"]))
    asked = await _agent_asks(pod_id, channel_id, colleague["id"], "Which repos?")
    held = await _push_to(asked, colleague["id"])
    await _deliver(held.id)
    assert (await _row(held.id)).reason_code is DeliveryReasonCode.FOLDED

    await _said_minutes_ago([asked], 10)
    await window_ends_now(channel_id, colleague["id"])
    await run_pushes()
    later = await _push_to(asked, colleague["id"])
    await _deliver(later.id)

    assert (await _row(later.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert mail["to"] == [colleague["email"]]
    assert "Which repos?" in mail["text"]


async def test_a_question_is_pushed_at_once_when_the_app_is_not_open(
    authenticated_client, db_session, setting, message_store
):
    pod_id, colleague = setting
    channel_id = await _my_channel(db_session, pod_id, colleague["id"])
    asked = await _agent_asks(pod_id, channel_id, colleague["id"], "Which repos?")
    push = await _push_to(asked, colleague["id"])
    await _deliver(push.id)

    assert (await _row(push.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "Which repos?" in mail["text"]


async def test_a_held_question_read_but_unanswered_still_goes(
    authenticated_client, async_client, db_session, setting, message_store
):
    """Reading a question is not answering it: held and folded back, it
    still calls them, so having read it in the app does not drop it."""
    pod_id, colleague = setting
    channel_id = await _my_channel(db_session, pod_id, colleague["id"])
    await mark_online(UUID(pod_id), UUID(colleague["id"]))
    asked = await _agent_asks(pod_id, channel_id, colleague["id"], "Which repos?")
    held = await _push_to(asked, colleague["id"])
    await _deliver(held.id)
    read = await async_client.post(
        f"/pods/{pod_id}/channels/{channel_id}/read",
        json={"seq": held.seq},
        headers=auth_headers(colleague),
    )
    assert read.status_code == 200, read.text

    await _said_minutes_ago([asked], 10)
    await window_ends_now(channel_id, colleague["id"])
    await run_pushes()
    later = await _push_to(asked, colleague["id"])
    assert later.urgent
    await _deliver(later.id)

    assert (await _row(later.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "Which repos?" in mail["text"]


async def test_a_held_question_in_a_group_leads_what_came_meanwhile(
    authenticated_client, setting, message_store
):
    """A plain group line said during the hold waits with the question, and
    the push leads with the question -- still one that may go by email."""
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)
    await mark_online(UUID(pod_id), UUID(colleague["id"]))
    asked = await _agent_asks(pod_id, group, colleague["id"], "Which repos?")
    held = await _push_to(asked, colleague["id"])
    await _deliver(held.id)
    assert (await _row(held.id)).reason_code is DeliveryReasonCode.FOLDED
    plain = await member_writes(authenticated_client, pod_id, group, "Lunch at one.")
    assert await _pushes_to(plain["id"], colleague["id"]) == []
    assert message_store.get_all("RESEND") == []

    await _said_minutes_ago([asked, plain["id"]], 10)
    await window_ends_now(group, colleague["id"])
    await run_pushes()
    together = await _push_to(asked, colleague["id"])
    assert (together.urgent, together.lines) == (True, 2)
    await _deliver(together.id)

    assert (await _row(together.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "Which repos?" in mail["text"]
