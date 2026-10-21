"""Who a line names is what its parts say now; why a person is in a channel
decides who may take them out.

Mentions: an edit that names somebody counts for them and tells them, one
that takes the name out stops counting; a deleted line names nobody; and
somebody named where they have no seat is counted from the place naming gave
them. Seats: the platform takes back only the seats it gave -- not one a
member added, even once that member is gone, and not one taken by speaking.

Against Postgres, through the API and the channels' repositories.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select, update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import Principal, SeatedBy, channel_by_id
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
)
from app.modules.channels.tests.e2e.channel_pod import Pod, group, say
from app.modules.channels.tests.e2e.push_support import pushed, run_pushes

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _naming(person: dict) -> list[dict[str, str]]:
    return [{"type": "mention", "kind": "MEMBER", "id": person["id"], "name": "x"}]


async def _mentions_of(pod: Pod, who: dict) -> int:
    inbox = await pod.client(who).get(f"/pods/{pod.id}/inbox")
    assert inbox.status_code == 200, inbox.text
    return int(inbox.json()["totals"]["mentions"])


async def _named_on(message_id: str) -> int:
    async with async_session_maker() as session:
        return int(
            await session.scalar(
                select(func.count()).where(
                    ChannelMentionModel.message_id == UUID(message_id)
                )
            )
            or 0
        )


async def test_an_edit_that_names_somebody_counts_and_tells_them_and_unnaming_stops(
    pod: Pod,
):
    crew = await group(pod, members=[pod.editor["id"]])
    line = await say(pod, crew["id"], "Can somebody check?")
    path = f"{pod.base}/{crew['id']}/messages/{line['id']}"
    await run_pushes()
    assert await _mentions_of(pod, pod.editor) == 0

    named = await pod.client().patch(
        path, json={"text": "Can you check?", "parts": _naming(pod.editor)}
    )
    assert named.status_code == 200, named.text
    await run_pushes()

    assert await _mentions_of(pod, pod.editor) == 1
    assert pod.editor["id"] in await pushed(line["id"])

    unnamed = await pod.client().patch(
        path, json={"text": "Can somebody check?", "parts": []}
    )
    assert unnamed.status_code == 200, unnamed.text
    assert await _mentions_of(pod, pod.editor) == 0
    assert await _named_on(line["id"]) == 0


async def test_a_deleted_line_names_nobody(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    line = await say(pod, crew["id"], "Can you check?", parts=_naming(pod.editor))
    assert await _mentions_of(pod, pod.editor) == 1

    gone = await pod.client().delete(f"{pod.base}/{crew['id']}/messages/{line['id']}")
    assert gone.status_code in (200, 204), gone.text

    assert await _named_on(line["id"]) == 0
    assert await _mentions_of(pod, pod.editor) == 0


async def test_somebody_named_in_a_group_they_never_joined_is_counted(pod: Pod):
    """Their mentions are counted from their own places in the pod; naming
    them gave them one there, with nothing read."""
    square = await group(pod, title="Town square")
    await say(pod, square["id"], "What do you think?", parts=_naming(pod.viewer))

    assert await _mentions_of(pod, pod.viewer) == 1
    listed = await pod.client(pod.viewer).get(pod.base)
    unread = {item["id"]: item["unread"] for item in listed.json()["items"]}
    assert unread[square["id"]]["mentions"] == 1


async def test_the_platform_takes_back_only_the_seats_it_gave(pod: Pod, db_session):
    """A member another member added stays when the platform says they left
    -- even once the member who added them is gone (their id is SET NULL) --
    and so does one who spoke there in Lemma; one the platform seated goes."""
    crew = await group(pod, members=[pod.editor["id"]])
    channel_id = UUID(crew["id"])
    added, spoke, mirrored = (
        UUID(pod.editor["id"]),
        UUID(pod.user["id"]),
        UUID(pod.viewer["id"]),
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    channel = await channel_by_id(uow, channel_id)
    assert channel is not None
    members = MemberRepository(db_session)
    await members.add_person(channel, Principal.member(spoke), seated_by=SeatedBy.SPOKE)
    await members.add_person(
        channel, Principal.member(mirrored), seated_by=SeatedBy.PLATFORM
    )
    # What the adder's delete does to the seat they made.
    await db_session.execute(
        update(ChannelMemberModel)
        .where(ChannelMemberModel.channel_id == channel_id)
        .values(added_by_user_id=None)
    )
    await db_session.commit()

    for user_id, taken_back in ((added, False), (spoke, False), (mirrored, True)):
        assert (
            await members.remove_platform_seat(channel_id, Principal.member(user_id))
            is taken_back
        )
    await db_session.commit()
    seated = set(
        await db_session.scalars(
            select(ChannelMemberModel.user_id).where(
                ChannelMemberModel.channel_id == channel_id
            )
        )
    )
    assert {added, spoke} <= seated and mirrored not in seated


async def test_a_seat_taken_again_for_a_stronger_reason_keeps_it(pod: Pod, db_session):
    """The platform seats somebody, then a member adds them: the pod chose
    them, so the platform no longer takes them back."""
    crew = await group(pod)
    channel = await channel_by_id(SqlAlchemyUnitOfWork(db_session), UUID(crew["id"]))
    assert channel is not None
    members = MemberRepository(db_session)
    person = Principal.member(UUID(pod.user["id"]))
    assert await members.add_person(channel, person, seated_by=SeatedBy.PLATFORM)
    assert not await members.add_person(channel, person, seated_by=SeatedBy.ADDED)
    assert not await members.add_person(channel, person, seated_by=SeatedBy.PLATFORM)
    await db_session.commit()

    seat = await members.of_user(channel.id, UUID(pod.user["id"]))
    assert seat is not None and seat.seated_by is SeatedBy.ADDED
    assert not await members.remove_platform_seat(channel.id, person)
