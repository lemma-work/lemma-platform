"""Two contacts turn out to be one: their channels become the survivor's.

Everything that names the merged contact names the survivor afterwards --
what they said, the questions put to them, the lines that mention them, their
seat -- and their channel's sources, lines, questions, mentions, seats and
conversations move into the survivor's, both channels' lines numbered again
together in the order they were said, with what each member had read kept. The merged channel is gone. Runs through the ``ContactMerged`` handler,
as the worker does.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import FastContext
from app.modules.agent.contracts.contact_conversations import repoint_merged_contact
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.channels.contracts import (
    AccountKey,
    ContactsHome,
    NewLine,
    NewQuestion,
    Place,
    Principal,
    QuestionKind,
    Sender,
    Source,
    SourceKind,
    post,
    source_for,
)
from app.modules.channels.events.contact_merged_work import fold_channels
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelSourceModel,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.contacts.contracts.facts import ContactMerged

from app.modules.channels.tests.e2e.channel_pod import an_asker

pytestmark = pytest.mark.e2e


async def _contact(uow: SqlAlchemyUnitOfWork, pod_id: UUID, number: str) -> Source:
    """A contact who wrote once from ``number``, and their channel's place."""
    contact = await open_contact(
        uow,
        pod_id=pod_id,
        kind=IdentityKind.TELEGRAM,
        value=number,
        strength=IdentityStrength.CHANNEL,
        display_name=None,
    )
    source = await source_for(
        uow,
        pod_id=pod_id,
        place=Place(
            surface_id=None,
            platform="TELEGRAM",
            account=AccountKey(workspace="bot-1"),
            kind=SourceKind.PERSON,
            external_channel_id=None,
            external_user_id=number,
        ),
        home=ContactsHome(contact.id),
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.contact(contact.id),
            text=f"hello from {number}",
            source_id=source.id,
        ),
    )
    return source


async def _contact_of(session: AsyncSession, source: Source) -> UUID:
    channel = await session.get(ChannelModel, source.channel_id)
    assert channel is not None and channel.contact_id is not None
    return channel.contact_id


async def _merge(session: AsyncSession, *, merged: UUID, into: UUID) -> None:
    """What ``ContactRepository.merge`` leaves: everything that was ``merged``
    points straight at ``into``."""
    await session.execute(
        text(
            "UPDATE contacts SET merged_into_id = :into"
            " WHERE id = :merged OR merged_into_id = :merged"
        ),
        {"into": into, "merged": merged},
    )


async def _fold(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, *, merged: UUID, survivor: UUID
) -> None:
    await fold_channels(
        cast(FastContext, SimpleNamespace(uow=uow)),
        ContactMerged(pod_id=pod_id, survivor_id=survivor, merged_id=merged),
    )


async def _timeline(session: AsyncSession, channel_id: UUID) -> list[tuple[int, str]]:
    session.expire_all()
    rows = await session.execute(
        select(ChannelMessageModel.seq, ChannelMessageModel.body)
        .where(ChannelMessageModel.channel_id == channel_id)
        .order_by(ChannelMessageModel.seq)
    )
    return [(int(seq), body) for seq, body in rows]


async def test_a_merged_contacts_channel_and_everything_naming_them_move_over(
    scenario, db_session: AsyncSession
):
    await scenario.create_org_with_pod(name_prefix="Channel fold")
    pod_id = UUID(scenario.pod_id)
    owner = UUID(scenario.owner_user["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    kept = await _contact(uow, pod_id, "5551001")
    folded = await _contact(uow, pod_id, "5551002")
    survivor = await _contact_of(db_session, kept)
    merged = await _contact_of(db_session, folded)
    asked = await post(
        uow,
        channel_id=folded.channel_id,
        line=NewLine(sender=Sender.member(owner), text="Which order?"),
        question=NewQuestion(
            kind=QuestionKind.ASK,
            ref="e2e:fold",
            asker=await an_asker(pod_id, owner),
            question={"prompt": "Which order?"},
            awaits=Principal.contact(merged),
        ),
        mentions=[Principal.contact(merged)],
    )
    assert asked is not None
    await _merge(db_session, merged=merged, into=survivor)
    await db_session.commit()

    await _fold(uow, pod_id, merged=merged, survivor=survivor)
    await db_session.commit()

    db_session.expire_all()
    assert await db_session.get(ChannelModel, folded.channel_id) is None
    assert await _timeline(db_session, kept.channel_id) == [
        (1, "hello from 5551001"),
        (2, "hello from 5551002"),
        (3, "Which order?"),
    ]
    head = await db_session.get(ChannelModel, kept.channel_id)
    assert head is not None and head.last_seq == 3
    # The newest line outside a thread is the moved one: the list shows it.
    assert head.last_line_id == asked.id
    said = await db_session.scalar(
        select(ChannelMessageModel).where(
            ChannelMessageModel.body == "hello from 5551002"
        )
    )
    assert said is not None and said.sender_contact_id == survivor
    question = await db_session.get(ChannelQuestionModel, asked.id)
    assert question is not None
    assert (question.channel_id, question.awaits_contact_id) == (
        kept.channel_id,
        survivor,
    )
    mention = await db_session.scalar(
        select(ChannelMentionModel).where(ChannelMentionModel.message_id == asked.id)
    )
    assert mention is not None
    # The mention takes its line's new number: unread mentions count by it.
    assert (mention.channel_id, mention.contact_id, mention.seq) == (
        kept.channel_id,
        survivor,
        3,
    )
    source = await db_session.get(ChannelSourceModel, folded.id)
    assert source is not None and source.channel_id == kept.channel_id
    seats = (
        await db_session.scalars(
            select(ChannelMemberModel.contact_id).where(
                ChannelMemberModel.contact_id.in_([survivor, merged])
            )
        )
    ).all()
    assert seats == [survivor]


async def test_a_fold_keeps_the_merged_channels_people_conversations_and_state(
    scenario, db_session: AsyncSession
):
    """Seats, the assignee, whether it is open, whether agents answer, the
    conversations working in it, and a client id both channels used."""
    await scenario.create_org_with_pod(name_prefix="Channel fold state")
    pod_id = UUID(scenario.pod_id)
    owner = UUID(scenario.owner_user["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    kept = await _contact(uow, pod_id, "5552001")
    folded = await _contact(uow, pod_id, "5552002")
    survivor = await _contact_of(db_session, kept)
    merged = await _contact_of(db_session, folded)
    for source in (kept, folded):
        await post(
            uow,
            channel_id=source.channel_id,
            line=NewLine(
                sender=Sender.member(owner),
                text=f"retry-safe in {source.channel_id}",
                client_message_id="same-key",
            ),
        )
    await db_session.execute(
        text(
            "UPDATE channel_cases SET status = 'closed', closed_at = now(),"
            " assignee_user_id = NULL WHERE channel_id = :kept"
        ),
        {"kept": kept.channel_id},
    )
    await db_session.execute(
        text(
            "UPDATE channel_cases SET status = 'open',"
            " assignee_user_id = :owner, assigned_at = now() WHERE channel_id = :folded"
        ),
        {"owner": owner, "folded": folded.channel_id},
    )
    await db_session.execute(
        text("UPDATE channels SET agents_answer = false WHERE id = :folded"),
        {"folded": folded.channel_id},
    )
    watcher = await scenario.create_user("fold-watcher")
    await scenario.add_user_to_pod(user=watcher, role="POD_EDITOR")
    watcher_id = UUID(watcher["id"])
    db_session.add(
        ChannelMemberModel(
            channel_id=folded.channel_id,
            pod_id=pod_id,
            user_id=watcher_id,
            role="member",
            seated_by="ADDED",
        )
    )
    conversation = ConversationModel(
        user_id=owner, pod_id=pod_id, contact_id=merged, channel_id=folded.channel_id
    )
    ongoing = ConversationModel(
        user_id=owner, pod_id=pod_id, contact_id=survivor, channel_id=kept.channel_id
    )
    db_session.add_all([conversation, ongoing])
    await db_session.flush()
    conversation_id, ongoing_id = conversation.id, ongoing.id
    await _merge(db_session, merged=merged, into=survivor)
    await db_session.commit()

    await _fold(uow, pod_id, merged=merged, survivor=survivor)
    await db_session.commit()

    db_session.expire_all()
    head = await db_session.get(ChannelModel, kept.channel_id)
    assert head is not None and head.current_case is not None
    # The survivor's own case was closed: the merged one's, open, goes on.
    case = head.current_case
    assert (case.status, head.agents_answer, case.assignee_user_id) == (
        "open",
        False,
        owner,
    )
    seated = (
        await db_session.scalars(
            select(ChannelMemberModel.user_id).where(
                ChannelMemberModel.channel_id == kept.channel_id,
                ChannelMemberModel.user_id.is_not(None),
            )
        )
    ).all()
    assert set(seated) == {owner, watcher_id}
    keys = (
        await db_session.scalars(
            select(ChannelMessageModel.client_message_id)
            .where(
                ChannelMessageModel.channel_id == kept.channel_id,
                ChannelMessageModel.client_message_id.is_not(None),
            )
            .order_by(ChannelMessageModel.seq)
        )
    ).all()
    assert keys[0] == "same-key"
    assert keys[1] != "same-key" and keys[1].startswith("fold:")
    moved = await db_session.get(ConversationModel, conversation_id)
    assert moved is not None and moved.channel_id == kept.channel_id

    # The agent's half of the merge then names the survivor: one person has
    # one live conversation per agent in a channel, so theirs keeps the seat
    # and the moved one leaves the channel, to be continued no more.
    await repoint_merged_contact(
        uow, pod_id=pod_id, merged_id=merged, survivor_id=survivor
    )
    await db_session.commit()
    db_session.expire_all()
    unseated = await db_session.get(ConversationModel, conversation_id)
    continued = await db_session.get(ConversationModel, ongoing_id)
    assert unseated is not None and continued is not None
    assert (unseated.contact_id, unseated.channel_id) == (survivor, None)
    assert (continued.contact_id, continued.channel_id) == (
        survivor,
        kept.channel_id,
    )


async def test_a_chained_merge_folds_into_whoever_survives_now(
    scenario, db_session: AsyncSession
):
    """A was merged into B, then B into C, and B's fold ran first: A's
    channel goes to C's, and no channel is made for B, who is nobody now."""
    await scenario.create_org_with_pod(name_prefix="Channel fold chain")
    pod_id = UUID(scenario.pod_id)
    uow = SqlAlchemyUnitOfWork(db_session)
    a, b, c = [
        await _contact(uow, pod_id, number)
        for number in ("5553001", "5553002", "5553003")
    ]
    first, second, third = [await _contact_of(db_session, s) for s in (a, b, c)]
    await _merge(db_session, merged=first, into=second)
    await _merge(db_session, merged=second, into=third)
    await db_session.commit()

    await _fold(uow, pod_id, merged=second, survivor=third)
    await _fold(uow, pod_id, merged=first, survivor=second)
    await db_session.commit()

    db_session.expire_all()
    contacts_channels = (
        await db_session.scalars(
            select(ChannelModel.contact_id).where(
                ChannelModel.pod_id == pod_id, ChannelModel.kind == "CONTACT"
            )
        )
    ).all()
    assert contacts_channels == [third]
    # In the order they were said, whichever fold ran first.
    assert await _timeline(db_session, c.channel_id) == [
        (1, "hello from 5553001"),
        (2, "hello from 5553002"),
        (3, "hello from 5553003"),
    ]
    speakers = set(
        (
            await db_session.scalars(
                select(ChannelMessageModel.sender_contact_id).where(
                    ChannelMessageModel.channel_id == c.channel_id
                )
            )
        ).all()
    )
    assert speakers == {third}


async def test_a_line_written_while_the_fold_runs_is_moved_not_lost(scenario):
    """A line being written to the merged channel holds its lock; the fold
    waits for it and moves it, rather than dropping the channel under it."""
    await scenario.create_org_with_pod(name_prefix="Channel fold race")
    pod_id = UUID(scenario.pod_id)
    factory = get_uow_factory()
    async with factory() as uow:
        kept = await _contact(uow, pod_id, "5554001")
        folded = await _contact(uow, pod_id, "5554002")
        survivor = await _contact_of(uow.session, kept)
        merged = await _contact_of(uow.session, folded)
        await _merge(uow.session, merged=merged, into=survivor)
        await uow.commit()

    async with factory() as writer:
        await post(
            writer,
            channel_id=folded.channel_id,
            line=NewLine(
                sender=Sender.contact(merged),
                text="written during the fold",
                source_id=folded.id,
            ),
        )

        async def fold() -> None:
            async with factory() as uow:
                await _fold(uow, pod_id, merged=merged, survivor=survivor)
                await uow.commit()

        folding = asyncio.create_task(fold())
        await asyncio.sleep(0.5)
        await writer.commit()
    await asyncio.wait_for(folding, timeout=10)

    async with factory() as uow:
        assert await _timeline(uow.session, kept.channel_id) == [
            (1, "hello from 5554001"),
            (2, "hello from 5554002"),
            (3, "written during the fold"),
        ]
        # The head counts it too, so the next line is 4, not a duplicate 3.
        head = await uow.session.get(ChannelModel, kept.channel_id)
        assert head is not None and head.last_seq == 3


async def test_a_fold_numbers_both_timelines_by_when_said_and_keeps_what_was_read(
    scenario, db_session: AsyncSession
):
    """Lines of the two channels interleave in the order they were said, a
    mention keeps its line's new number, and a member who had read both up
    to some line has read the merged one up to just before the first line
    they had not -- not every moved line unread again."""
    await scenario.create_org_with_pod(name_prefix="Channel fold order")
    pod_id = UUID(scenario.pod_id)
    owner = UUID(scenario.owner_user["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    kept = await _contact(uow, pod_id, "5555001")
    folded = await _contact(uow, pod_id, "5555002")
    survivor = await _contact_of(db_session, kept)
    merged = await _contact_of(db_session, folded)
    named = await post(
        uow,
        channel_id=kept.channel_id,
        line=NewLine(sender=Sender.member(owner), text="kept, later"),
        mentions=[Principal.contact(survivor)],
    )
    await post(
        uow,
        channel_id=folded.channel_id,
        line=NewLine(sender=Sender.member(owner), text="folded, later"),
    )
    assert named is not None
    # The owner read the kept channel to its end (2), the folded one only
    # its first line (1 of 2).
    await db_session.execute(
        text(
            "UPDATE channel_read_state SET last_read_seq = CASE channel_id"
            " WHEN :kept THEN 2 ELSE 1 END WHERE user_id = :owner"
        ),
        {"kept": kept.channel_id, "owner": owner},
    )
    await _merge(db_session, merged=merged, into=survivor)
    await db_session.commit()

    await _fold(uow, pod_id, merged=merged, survivor=survivor)
    await db_session.commit()

    db_session.expire_all()
    assert await _timeline(db_session, kept.channel_id) == [
        (1, "hello from 5555001"),
        (2, "hello from 5555002"),
        (3, "kept, later"),
        (4, "folded, later"),
    ]
    mention = await db_session.scalar(
        select(ChannelMentionModel).where(ChannelMentionModel.message_id == named.id)
    )
    assert mention is not None and (mention.channel_id, mention.seq) == (
        kept.channel_id,
        3,
    )
    read = await db_session.scalar(
        text(
            "SELECT last_read_seq FROM channel_read_state"
            " WHERE user_id = :owner AND channel_id = :kept"
        ),
        {"owner": owner, "kept": kept.channel_id},
    )
    # Read through "kept, later" (3); "folded, later" (4) is the first unread.
    assert read == 3
