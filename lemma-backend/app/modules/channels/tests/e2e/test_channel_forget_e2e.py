"""Forgetting a contact takes them out of the channels, not everybody else.

Their own channel goes with them. In a group they wrote in, their lines are
emptied in place -- said by nobody -- so a thread they started keeps the
members' replies and the agent's answers, the mentions and questions on those
lines stay, and the timeline keeps every number.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import (
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    Visibility,
    post,
)
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.tests.e2e.channel_pod import an_asker, contact_place
from app.modules.contacts.services.forget import forget_contact

pytestmark = pytest.mark.e2e


async def test_forgetting_a_contact_who_started_a_group_thread_keeps_the_replies(
    pod, db_session: AsyncSession
):
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    peer = UUID(pod.user["id"])
    place, dana = await contact_place(db_session, pod)
    uow = SqlAlchemyUnitOfWork(db_session)
    group = await ChannelRepository(db_session).open_group(
        pod_id=pod_id,
        title="orders",
        visibility=Visibility.PUBLIC,
        created_by_user_id=owner,
    )
    root = await post(
        uow,
        channel_id=group.id,
        line=NewLine(sender=Sender.contact(dana), text="My order is late"),
    )
    assert root is not None
    reply = await post(
        uow,
        channel_id=group.id,
        line=NewLine(
            sender=Sender.member(owner),
            text="Looking now -- can you check the warehouse?",
            thread_root_id=root.id,
        ),
        question=NewQuestion(
            kind=QuestionKind.ASK,
            ref="e2e:forget-thread",
            asker=await an_asker(pod_id, owner),
            question={"prompt": "Can you check the warehouse?"},
            awaits=Principal.member(peer),
        ),
        mentions=[Principal.member(peer)],
    )
    answer = await post(
        uow,
        channel_id=group.id,
        line=NewLine(
            sender=Sender.agent(pod_id, on_behalf_of=Principal.contact(dana)),
            text="It ships today.",
            thread_root_id=root.id,
        ),
    )
    assert reply is not None and answer is not None
    await db_session.commit()

    forgotten = await forget_contact(
        get_uow_factory(), pod_id=pod_id, contact_id=dana, forgotten_by=owner
    )

    assert forgotten is not None
    db_session.expire_all()
    lines = (
        await db_session.scalars(
            select(ChannelMessageModel)
            .where(ChannelMessageModel.channel_id == group.id)
            .order_by(ChannelMessageModel.seq)
        )
    ).all()
    assert [(line.seq, line.body) for line in lines] == [
        (1, ""),
        (2, "Looking now -- can you check the warehouse?"),
        (3, "It ships today."),
    ]
    emptied, kept_reply, kept_answer = lines
    assert emptied.id == root.id
    assert emptied.deleted_at is not None
    assert (emptied.sender_kind, emptied.sender_contact_id) == ("CONTACT", None)
    assert kept_reply.thread_root_id == root.id
    assert kept_answer.thread_root_id == root.id
    assert kept_answer.on_behalf_contact_id is None
    question = await db_session.get(ChannelQuestionModel, reply.id)
    assert question is not None and question.awaits_user_id == peer
    mention = await db_session.scalar(
        select(ChannelMentionModel.user_id).where(
            ChannelMentionModel.message_id == reply.id
        )
    )
    assert mention == peer
    # Their own channel went with them, and their seat in the group.
    assert await db_session.get(ChannelModel, place.channel_id) is None
    seat = await db_session.scalar(
        select(ChannelMemberModel.id).where(
            ChannelMemberModel.channel_id == group.id,
            ChannelMemberModel.contact_id.is_not(None),
        )
    )
    assert seat is None
