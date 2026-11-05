"""Who is pushed a line outside the app -- worked out once it is committed.

A busy group read by push buries the questions in it, so a group seat hears
only what names or asks it until its member asks for more; somebody named or
asked who is not in the group is told too, when they may see it; a line
withheld from a member -- an answer made with somebody else's access -- is
never pushed to them in full. However big the group, every seat is read.

A member is pushed once per channel per window: a line in the window after a
push waits for its end, and goes with whatever else came meanwhile, as one
push saying how many lines it stands for -- unless one names or asks them,
which goes at once and takes the waiting lines with it.

Against Postgres, through the API and the channels contracts; a push is a
delivery row naming the member (``recipient_user_id``), planned by the
``channels.pushes`` work a posted line causes (``push_support.run_pushes``).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.channels.contracts import (
    DeliveryReasonCode,
    DeliveryStatus,
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    post,
)
from app.modules.channels.contracts.deliveries import delivery_by_id, delivery_folded
from app.modules.channels.tests.e2e.channel_pod import (
    Pod,
    an_asker,
    group,
    keep_answers_to_the_asker,
    say,
)
from app.modules.channels.tests.e2e.push_support import (
    pushed,
    pushes_of,
    run_pushes,
    waiting,
    window_ends_now,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _naming(person: dict) -> list[dict[str, str]]:
    return [{"type": "mention", "kind": "MEMBER", "id": person["id"], "name": "x"}]


async def _hears_everything(pod: Pod, channel_id: str, who: dict) -> None:
    changed = await pod.client(who).patch(
        f"{pod.base}/{channel_id}/members/me", json={"notify": "ALL"}
    )
    assert changed.status_code == 200, changed.text


async def test_a_group_tells_its_members_only_what_names_them_until_they_ask(
    pod: Pod,
):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])

    plain = await say(pod, crew["id"], "Standup moved to ten.")
    named = await say(pod, crew["id"], "Can you check?", parts=_naming(pod.editor))
    # Worked out as they were said, before the user asks for more.
    await run_pushes()
    await _hears_everything(pod, crew["id"], pod.user)
    later = await say(pod, crew["id"], "Thanks all.")

    assert await pushed(plain["id"]) == set()
    assert await pushed(named["id"]) == {pod.editor["id"]}
    assert await pushed(later["id"]) == {pod.user["id"]}


async def test_somebody_named_or_asked_who_is_not_in_a_group_is_told(pod: Pod):
    """The viewer is not in the public group: naming them, or asking them,
    still reaches them -- they may read it."""
    square = await group(pod, title="Town square")

    named = await say(
        pod, square["id"], "What do you think?", parts=_naming(pod.viewer)
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        asked = await post(
            uow,
            channel_id=UUID(square["id"]),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id), on_behalf_of=Principal.member(UUID(pod.owner["id"]))
                ),
                text="Approve the venue?",
            ),
            question=NewQuestion(
                kind=QuestionKind.ASK,
                ref=f"msg:{uuid4()}",
                asker=await an_asker(UUID(pod.id), UUID(pod.owner["id"])),
                question={"prompt": "Approve the venue?"},
                awaits=Principal.member(UUID(pod.viewer["id"])),
            ),
        )
        await uow.commit()

    assert asked is not None
    assert await pushed(named["id"]) == {pod.viewer["id"]}
    assert await pushed(asked.id) == {pod.viewer["id"]}
    assert (await pushes_of(asked.id))[pod.viewer["id"]].urgent


async def _owners_answer(channel_id: str, pod: Pod) -> UUID:
    """The agent's answer made with the owner's own access, naming the editor."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        answer = await post(
            uow,
            channel_id=UUID(channel_id),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id), on_behalf_of=Principal.member(UUID(pod.owner["id"]))
                ),
                text="Your salary review is on Friday.",
            ),
            mentions=[Principal.member(UUID(pod.editor["id"]))],
        )
        await uow.commit()
    assert answer is not None
    return answer.id


async def test_a_line_withheld_from_a_member_is_never_pushed_to_them(pod: Pod):
    """An answer the agent made with the owner's own access, in a group that
    keeps such answers to the asker: the editor sees only that it was given
    -- and is not pushed what it said, even named in it."""
    crew = await group(pod, members=[pod.editor["id"]])
    await keep_answers_to_the_asker(pod, crew["id"])
    await _hears_everything(pod, crew["id"], pod.editor)

    answer = await _owners_answer(crew["id"], pod)

    assert pod.editor["id"] not in await pushed(answer)


async def test_an_answer_a_group_shows_everybody_is_pushed_like_any_line(pod: Pod):
    """The same answer in a group that shows its agents' answers (the
    default): the editor reads it, so it is pushed to them."""
    crew = await group(pod, members=[pod.editor["id"]])
    await _hears_everything(pod, crew["id"], pod.editor)

    answer = await _owners_answer(crew["id"], pod)

    assert pod.editor["id"] in await pushed(answer)


async def test_a_busy_channel_pushes_a_member_once_until_a_line_calls_them(
    pod: Pod,
):
    """The first line goes at once; the next two wait out the window; a line
    naming them goes now and carries the two that waited -- one push of
    three lines, saying it calls them."""
    crew = await group(pod, members=[pod.user["id"]])
    await _hears_everything(pod, crew["id"], pod.user)
    user = pod.user["id"]

    first = await say(pod, crew["id"], "Deploy at noon.")
    second = await say(pod, crew["id"], "Moved to one.")
    third = await say(pod, crew["id"], "Now two.")
    first_push = (await pushes_of(first["id"]))[user]
    assert (first_push.lines, first_push.urgent) == (1, False)
    assert await pushed(second["id"]) == await pushed(third["id"]) == set()
    assert await waiting(crew["id"], user) == 2

    named = await say(pod, crew["id"], "Can you run it?", parts=_naming(pod.user))
    push = (await pushes_of(named["id"]))[user]

    assert (push.from_seq, push.seq, push.lines) == (second["seq"], named["seq"], 3)
    assert push.urgent
    assert await waiting(crew["id"], user) == 0


async def test_lines_that_waited_out_the_window_go_as_one_push(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    await _hears_everything(pod, crew["id"], pod.user)
    user = pod.user["id"]
    await say(pod, crew["id"], "One.")
    second = await say(pod, crew["id"], "Two.")
    third = await say(pod, crew["id"], "Three.")
    await run_pushes()

    await window_ends_now(crew["id"], user)
    push = (await pushes_of(third["id"]))[user]

    assert (push.from_seq, push.lines, push.urgent) == (second["seq"], 2, False)
    assert push.status is DeliveryStatus.PENDING
    assert await waiting(crew["id"], user) == 0


async def test_a_push_the_budget_held_goes_with_the_next_whatever_it_says(
    pod: Pod,
):
    """A held push is found by why it was held, not by the sentence beside
    it: reworded, its line still goes with the member's next push."""
    crew = await group(pod, members=[pod.user["id"]])
    await _hears_everything(pod, crew["id"], pod.user)
    user = pod.user["id"]
    held = await say(pod, crew["id"], "Standup notes are up.")
    push = (await pushes_of(held["id"]))[user]
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await delivery_folded(
            uow, push, reason="Held for now.", hold_for=timedelta(minutes=30)
        )
        await uow.commit()
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE channel_message_deliveries SET reason = 'Reworded since.' "
                "WHERE id = :id"
            ),
            {"id": push.id},
        )
        await session.commit()
    assert await waiting(crew["id"], user) == 1

    named = await say(pod, crew["id"], "Read them?", parts=_naming(pod.user))
    carried = (await pushes_of(named["id"]))[user]

    assert (carried.from_seq, carried.lines) == (held["seq"], 2)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        folded = await delivery_by_id(uow, push.id)
    assert folded is not None
    assert (folded.status, folded.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.FOLDED,
    )


async def test_every_seat_of_a_big_group_is_pushed(pod: Pod):
    """Six hundred seats who hear everything, and the user -- seated last,
    hearing only what names them -- named: all of them pushed, past any one
    page of seats."""
    crew = await group(pod, members=[pod.user["id"]])
    seats = 600
    async with async_session_maker() as session:
        await session.execute(
            text(
                "WITH people AS ("
                "  INSERT INTO users (id, email, is_verified, is_active, "
                "    is_superuser, is_deleted, created_at, updated_at) "
                "  SELECT gen_random_uuid(), 'seat-' || n || '-' || :tag || "
                "    '@example.com', true, true, false, false, now(), now() "
                "  FROM generate_series(1, :seats) AS n RETURNING id) "
                "INSERT INTO channel_members (id, channel_id, pod_id, user_id, "
                "  role, notify, seated_by, created_at, updated_at) "
                "SELECT gen_random_uuid(), :channel, :pod, people.id, 'member', "
                "  'ALL', 'ADDED', now() - interval '1 day', now() FROM people"
            ),
            {
                "tag": uuid4().hex[:8],
                "seats": seats,
                "channel": UUID(crew["id"]),
                "pod": UUID(pod.id),
            },
        )
        await session.commit()

    line = await say(pod, crew["id"], "All hands at four.", parts=_naming(pod.user))
    told = await pushes_of(line["id"])

    assert len(told) == seats + 1
    assert told[pod.user["id"]].urgent
