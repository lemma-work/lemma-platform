"""The edges of writing in channels: replays, names, owners and places.

* A create replayed by its ``Idempotency-Key`` returns what it made only to
  somebody who may still see it.
* A client's name for a line is the same line when sent again -- and refused
  when the same name now asks for a different line; it is the client's own,
  so it can never take a name the pod writes its own lines by.
* A group's owner keeps the say over it only while they may write in the pod.
* A line hangs only from threads, lines and places of its own channel.

Against Postgres, through the API, and the channels contracts for what only
the pod's own code writes.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.channels.contracts import (
    NewLine,
    Principal,
    Sender,
    SenderKind,
    post,
)
from app.modules.channels.services.posting import LineMisplaced
from app.modules.channels.tests.e2e.channel_pod import (
    Pod,
    contact_place,
    group,
    say,
)

pytestmark = pytest.mark.e2e


async def test_a_replayed_create_shows_the_group_only_to_who_may_still_see_it(
    pod: Pod,
):
    key = {"Idempotency-Key": f"made-{uuid4()}"}
    body = {
        "title": "Quiet room",
        "visibility": "PRIVATE",
        "members": [pod.editor["id"]],
    }
    user = pod.client(pod.user)
    made = await user.post(pod.base, json=body, headers=key)
    assert made.status_code == 201, made.text
    again = await user.post(pod.base, json=body, headers=key)
    assert again.status_code == 201, again.text
    assert again.json()["id"] == made.json()["id"]

    # The group keeps an owner: the editor takes it over first.
    promoted = await pod.client(pod.editor).patch(
        f"{pod.base}/{made.json()['id']}/members/MEMBER:{pod.editor['id']}",
        json={"role": "owner"},
    )
    assert promoted.status_code == 200, promoted.text
    removed = await pod.client(pod.editor).delete(
        f"{pod.base}/{made.json()['id']}/members/MEMBER:{pod.user['id']}"
    )
    assert removed.status_code == 204, removed.text
    replayed = await user.post(pod.base, json=body, headers=key)

    assert replayed.status_code == 404, replayed.text
    assert "Quiet room" not in replayed.text


async def test_a_line_name_sent_again_is_the_line_and_reused_for_another_is_refused(
    pod: Pod,
):
    crew = await group(pod)
    first = await say(pod, crew["id"], "hello", client_message_id="c-1")
    again = await say(pod, crew["id"], "hello", client_message_id="c-1")
    other = await pod.client().post(
        f"{pod.base}/{crew['id']}/messages",
        json={"text": "something else", "client_message_id": "c-1"},
    )

    assert again["id"] == first["id"]
    assert other.status_code == 422, other.text
    listed = await pod.client().get(f"{pod.base}/{crew['id']}/messages")
    assert [line["text"] for line in listed.json()["items"]] == ["hello"]


async def test_a_client_never_takes_a_name_the_pod_writes_its_own_lines_by(
    pod: Pod,
):
    """A run's answer is the line ``run:{run_id}``; a member naming their own
    line so first gets their line, and the answer is still its own."""
    crew = await group(pod)
    taken = f"run:{uuid4()}"
    mine = await say(pod, crew["id"], "first!", client_message_id=taken)

    async with create_uow_from_session_maker(async_session_maker) as uow:
        answer = await post(
            uow,
            channel_id=UUID(crew["id"]),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id), on_behalf_of=Principal.member(UUID(pod.owner["id"]))
                ),
                text="The answer.",
                client_message_id=taken,
            ),
        )
        await uow.commit()

    assert answer is not None
    assert answer.id != UUID(mine["id"])
    assert answer.sender.kind is SenderKind.AGENT
    assert answer.client_message_id == taken


async def test_an_owner_made_a_viewer_keeps_the_seat_not_the_say(pod: Pod):
    scenario = pod.scenario
    person = await scenario.create_user("chv2-demoted")
    membership = await scenario.add_user_to_pod(user=person, role="POD_USER")
    theirs = await group(pod, as_user=person, members=[pod.editor["id"]])

    demoted = await pod.client().patch(
        f"/pods/{pod.id}/members/{membership['pod_member_id']}/roles",
        json={"roles": ["POD_VIEWER"]},
    )
    assert demoted.status_code == 200, demoted.text
    removing = await pod.client(person).delete(
        f"{pod.base}/{theirs['id']}/members/MEMBER:{pod.editor['id']}"
    )

    assert removing.status_code == 403, removing.text
    seat = await pod.client(pod.editor).get(f"{pod.base}/{theirs['id']}")
    assert seat.json()["me"]["joined"] is True
    leaving = await pod.client(person).delete(f"{pod.base}/{theirs['id']}/members/me")
    assert leaving.status_code == 204, leaving.text


async def test_a_line_hangs_only_from_threads_and_places_of_its_own_channel(
    pod: Pod, db_session: AsyncSession
):
    here = await group(pod, title="Here")
    there = await group(pod, title="There")
    elsewhere = await say(pod, there["id"], "a thread over there")
    contacts_place, _ = await contact_place(db_session, pod)

    owner = Sender.member(UUID(pod.owner["id"]))
    # Each refused in a transaction of its own: the database refuses a thread
    # or a place of another channel (the lines' composite foreign keys), and
    # the transaction it refused is over.
    misplaced = [
        (
            NewLine(
                sender=owner,
                text="replying across channels",
                thread_root_id=UUID(elsewhere["id"]),
            ),
            (),
        ),
        (
            NewLine(
                sender=owner,
                text="answering in reply to a line over there",
                reply_to_id=UUID(elsewhere["id"]),
            ),
            (),
        ),
        (
            NewLine(sender=owner, text="heard elsewhere", source_id=contacts_place.id),
            (),
        ),
        (NewLine(sender=owner, text="to somebody else's contact"), [contacts_place.id]),
    ]
    for line, deliver_to in misplaced:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            with pytest.raises(LineMisplaced):
                await post(
                    uow,
                    channel_id=UUID(here["id"]),
                    line=line,
                    deliver_to=deliver_to,
                )
            await uow.rollback()
