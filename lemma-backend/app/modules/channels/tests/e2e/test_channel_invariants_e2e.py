"""Two promises every read of channels stands on, held against Postgres.

* **The timeline is numbered without gaps.** Lines written at once, each in
  its own transaction, take 1, 2, 3 ... in their channel, and a line sent
  twice at once is one line.
* **One rule, twice, says the same.** ``domain.access.may_see`` and its SQL
  twin ``infrastructure.access.visible_to`` agree for every kind of channel
  and every kind of reader -- in it or not, holding ``contact.read`` or not,
  an admin -- and so do ``domain.projection.withheld_from`` and its twin
  ``shown_in_full_to`` for every kind of line.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.channels.contracts import (
    Channel,
    ChannelMessage,
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    channel_by_id,
    direct_channel,
    post,
)
from app.modules.channels.domain.access import Viewer, may_see
from app.modules.channels.domain.projection import withheld_from
from app.modules.channels.infrastructure.access import shown_in_full_to, visible_to
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
)
from app.modules.channels.tests.e2e.channel_pod import (
    Pod,
    an_asker,
    contact_place,
    group,
)
from app.modules.pod.contracts.members import pod_member_holds

pytestmark = pytest.mark.e2e

#: Lines written at once into one channel.
WRITERS = 16


async def _write(channel_id: UUID, sender: Sender, client_id: str) -> ChannelMessage:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await post(
            uow,
            channel_id=channel_id,
            line=NewLine(sender=sender, text=client_id, client_message_id=client_id),
        )
        await uow.commit()
    assert line is not None
    return line


async def test_lines_written_at_once_are_numbered_without_gaps(pod: Pod):
    crew = await group(pod)
    channel_id = UUID(crew["id"])
    owner = Sender.member(UUID(pod.owner["id"]))
    twice = f"twice-{uuid4()}"

    written = await asyncio.gather(
        *(_write(channel_id, owner, f"line-{n}") for n in range(WRITERS)),
        *(_write(channel_id, owner, twice) for _ in range(3)),
    )

    async with async_session_maker() as session:
        seqs = list(
            await session.scalars(
                select(ChannelMessageModel.seq)
                .where(ChannelMessageModel.channel_id == channel_id)
                .order_by(ChannelMessageModel.seq)
            )
        )
        head = await session.scalar(
            select(ChannelModel.last_seq).where(ChannelModel.id == channel_id)
        )
    assert seqs == list(range(1, WRITERS + 2))
    assert head == WRITERS + 1
    assert len({line.id for line in written[WRITERS:]}) == 1, "one line per name"


# ------------------------------------------------------- the rule and its twin


async def _viewer(uow: SqlAlchemyUnitOfWork, pod_id: UUID, user_id: UUID) -> Viewer:
    return Viewer(
        user_id=user_id,
        pod_id=pod_id,
        sees_contacts=await pod_member_holds(uow, pod_id, user_id, "contact.read"),
    )


async def _channels(db_session: AsyncSession, pod: Pod) -> list[Channel]:
    """One of every kind a reader meets: a public group; a private group with
    the editor in it; a private group of the owner's alone; the owner's own
    channel and the user's; a contact's."""
    public = await group(pod, title="Open")
    shared = await group(
        pod, title="Shared", visibility="PRIVATE", members=[pod.editor["id"]]
    )
    alone = await group(pod, title="Alone", visibility="PRIVATE")
    source, _ = await contact_place(db_session, pod)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        pod_id = UUID(pod.id)
        made = [
            await direct_channel(uow, pod_id=pod_id, user_id=UUID(pod.owner["id"])),
            await direct_channel(uow, pod_id=pod_id, user_id=UUID(pod.user["id"])),
        ]
        await uow.commit()
        for channel_id in (public["id"], shared["id"], alone["id"], source.channel_id):
            found = await channel_by_id(uow, UUID(str(channel_id)))
            assert found is not None
            made.append(found)
    return made


async def test_may_see_and_visible_to_agree_for_every_channel_and_reader(
    pod: Pod, db_session: AsyncSession
):
    channels = await _channels(db_session, pod)
    readers = [pod.owner, pod.editor, pod.user, pod.viewer]
    outcomes: set[bool] = set()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        members = MemberRepository(uow.session)
        for reader in readers:
            viewer = await _viewer(uow, UUID(pod.id), UUID(reader["id"]))
            in_python = {
                channel.id
                for channel in channels
                if may_see(
                    channel,
                    viewer,
                    is_member=await members.of_user(channel.id, viewer.user_id)
                    is not None,
                )
            }
            in_sql = set(
                await uow.session.scalars(
                    select(ChannelModel.id).where(
                        visible_to(viewer),
                        ChannelModel.id.in_([channel.id for channel in channels]),
                    )
                )
            )
            assert in_python == in_sql, reader["email"]
            outcomes |= {channel.id in in_python for channel in channels}
    assert outcomes == {True, False}, "the matrix shows both answers"


async def test_withheld_from_and_shown_in_full_to_agree_for_every_line(
    pod: Pod, db_session: AsyncSession
):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    _, contact_id = await contact_place(db_session, pod)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        own = await direct_channel(uow, pod_id=pod_id, user_id=owner)
        await uow.commit()

    async def says(
        channel_id: UUID, sender: Sender, question: NewQuestion | None = None
    ) -> UUID:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            line = await post(
                uow,
                channel_id=channel_id,
                line=NewLine(sender=sender, text=f"line {uuid4()}"),
                question=question,
            )
            await uow.commit()
        assert line is not None
        return line.id

    group_id = UUID(crew["id"])
    with_owners_access = Sender.agent(pod_id, on_behalf_of=Principal.member(owner))
    lines = [
        await says(group_id, Sender.member(owner)),
        await says(group_id, with_owners_access),
        await says(group_id, Sender.agent(pod_id, on_behalf_of=None)),
        await says(
            group_id, Sender.agent(pod_id, on_behalf_of=Principal.contact(contact_id))
        ),
        await says(
            group_id,
            with_owners_access,
            question=NewQuestion(
                kind=QuestionKind.ASK,
                ref=f"msg:{uuid4()}",
                asker=await an_asker(pod_id, owner),
                question={"prompt": "Which?"},
                awaits=Principal.member(UUID(pod.user["id"])),
            ),
        ),
        await says(own.id, with_owners_access),
    ]
    outcomes: set[bool] = set()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        found = await LineRepository(uow.session).by_ids(lines)
        for reader in (pod.owner, pod.editor, pod.user):
            viewer = await _viewer(uow, pod_id, UUID(reader["id"]))
            in_python = {
                line_id
                for line_id in lines
                if not withheld_from(
                    found[line_id],
                    user_id=viewer.user_id,
                    in_their_direct=found[line_id].channel_id == own.id
                    and viewer.user_id == owner,
                )
            }
            in_sql = set(
                await uow.session.scalars(
                    select(ChannelMessageModel.id)
                    .join(
                        ChannelModel, ChannelModel.id == ChannelMessageModel.channel_id
                    )
                    .outerjoin(
                        ChannelQuestionModel,
                        ChannelQuestionModel.message_id == ChannelMessageModel.id,
                    )
                    .where(ChannelMessageModel.id.in_(lines), shown_in_full_to(viewer))
                )
            )
            assert in_python == in_sql, reader["email"]
            outcomes |= {line_id in in_python for line_id in lines}
    assert outcomes == {True, False}
