"""How far a member has read, with threads in it.

* A channel's unread lines are its timeline's: replies count on their thread.
* A mention is read where it was said -- in the timeline, or in its thread.
  Reading the timeline to its end leaves a reply naming the reader unread;
  reading that thread reads it.
* Replying in a thread reads that thread, not the channel.
* Joining a channel starts at its newest line; what named them before stays.
* A channel muted for now is not among the channels with something new.
* Named in a channel one is not in, it is new to them from the line naming
  them until they read it; one member reading it leaves it new for another.
* The member looking after a bot's contacts is in a new contact's channel
  from the contact's first word.

Against Postgres, through the API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place, group, say

pytestmark = pytest.mark.e2e


def _naming(person: dict) -> list[dict[str, str]]:
    return [{"type": "mention", "kind": "MEMBER", "id": person["id"], "name": "x"}]


async def _unread(pod: Pod, channel_id: str, who: dict) -> dict:
    found = await pod.client(who).get(f"{pod.base}/{channel_id}")
    assert found.status_code == 200, found.text
    return found.json()["unread"]


async def _read(pod: Pod, channel_id: str, who: dict, seq: int, **body) -> dict:
    read = await pod.client(who).post(
        f"{pod.base}/{channel_id}/read", json={"seq": seq, **body}
    )
    assert read.status_code == 200, read.text
    return read.json()


async def test_replies_count_on_their_thread_not_in_the_timeline(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    root = await say(pod, crew["id"], "Plan for Tuesday")
    for n in range(3):
        await say(pod, crew["id"], f"reply {n}", thread_root_id=root["id"])

    assert (await _unread(pod, crew["id"], pod.user))["lines"] == 1
    await _read(pod, crew["id"], pod.user, root["seq"])
    assert (await _unread(pod, crew["id"], pod.user))["lines"] == 0
    inbox = await pod.client(pod.user).get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 0, inbox.text

    await say(pod, crew["id"], "Next topic")
    assert (await _unread(pod, crew["id"], pod.user))["lines"] == 1


async def test_a_mention_in_a_thread_is_read_in_its_thread(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    root = await say(pod, crew["id"], "Plan for Tuesday")
    named = await say(
        pod,
        crew["id"],
        "can you take this?",
        thread_root_id=root["id"],
        parts=_naming(pod.user),
    )
    last = await say(pod, crew["id"], "Next topic")

    # The timeline read to its end: the reply naming them is not read.
    await _read(pod, crew["id"], pod.user, last["seq"])
    unread = await _unread(pod, crew["id"], pod.user)
    assert (unread["lines"], unread["mentions"]) == (0, 1)

    # Read in its thread, it is -- and the timeline's number stays.
    read = await _read(
        pod, crew["id"], pod.user, named["seq"], thread_root_id=root["id"]
    )
    assert read["read_seq"] == last["seq"]
    assert (await _unread(pod, crew["id"], pod.user))["mentions"] == 0


async def test_replying_in_a_thread_reads_the_thread_not_the_channel(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    root = await say(pod, crew["id"], "Plan for Tuesday")
    await say(pod, crew["id"], "see this", parts=_naming(pod.user))
    await say(
        pod,
        crew["id"],
        "and this",
        thread_root_id=root["id"],
        parts=_naming(pod.user),
    )
    before = await _unread(pod, crew["id"], pod.user)
    assert before["mentions"] == 2

    await say(pod, crew["id"], "on it", as_user=pod.user, thread_root_id=root["id"])

    after = await _unread(pod, crew["id"], pod.user)
    # The thread is read; the line naming them in the timeline is not.
    assert after["read_seq"] == before["read_seq"]
    assert after["mentions"] == 1
    assert after["lines"] == before["lines"]


async def test_joining_starts_at_the_newest_line(pod: Pod):
    town = await group(pod, title="Town square")
    for n in range(5):
        await say(pod, town["id"], f"history {n}")
    named = await say(pod, town["id"], "ask the user", parts=_naming(pod.user))

    joined = await pod.client(pod.user).post(f"{pod.base}/{town['id']}/members/me")
    assert joined.status_code == 200, joined.text
    unread = joined.json()["unread"]
    assert unread["lines"] == 0
    assert unread["read_seq"] == named["seq"]
    # Named before they came: still theirs to read.
    assert unread["mentions"] == 1

    await say(pod, town["id"], "welcome")
    assert (await _unread(pod, town["id"], pod.user))["lines"] == 1


async def test_a_channel_muted_for_now_is_not_counted_as_new(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    await say(pod, crew["id"], "news")
    member = pod.client(pod.user)
    inbox = await member.get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 1, inbox.text

    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    muted = await member.patch(
        f"{pod.base}/{crew['id']}/members/me", json={"muted_until": until}
    )
    assert muted.status_code == 200, muted.text
    inbox = await member.get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 0, inbox.text

    ended = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await member.patch(
        f"{pod.base}/{crew['id']}/members/me", json={"muted_until": ended}
    )
    inbox = await member.get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 1, inbox.text


async def _totals(pod: Pod, who: dict) -> dict:
    inbox = await pod.client(who).get(f"/pods/{pod.id}/inbox")
    assert inbox.status_code == 200, inbox.text
    return inbox.json()["totals"]


async def test_a_member_named_where_they_are_not_is_told_from_that_line(pod: Pod):
    town = await group(pod, title="Town square")
    for n in range(3):
        await say(pod, town["id"], f"history {n}")
    before = await _totals(pod, pod.user)

    named = await say(pod, town["id"], "can you look?", parts=_naming(pod.user))

    # Not in it, but named: the channel is new to them from the line naming
    # them -- what was said before is history, as it is for joining.
    unread = await _unread(pod, town["id"], pod.user)
    assert (unread["lines"], unread["mentions"]) == (1, 1), unread
    totals = await _totals(pod, pod.user)
    assert totals["channels"] == before["channels"] + 1, totals
    assert totals["mentions"] == before["mentions"] + 1, totals

    await _read(pod, town["id"], pod.user, named["seq"])
    unread = await _unread(pod, town["id"], pod.user)
    assert (unread["lines"], unread["mentions"]) == (0, 0), unread
    assert await _totals(pod, pod.user) == before

    # Read, and never joined: what is said next is not theirs to hear of.
    await say(pod, town["id"], "thanks")
    assert (await _unread(pod, town["id"], pod.user))["lines"] == 0
    assert await _totals(pod, pod.user) == before


async def test_one_member_reading_leaves_it_new_for_another(pod: Pod):
    town = await group(pod, title="Town square")
    named = await say(
        pod, town["id"], "kickoff at 3", parts=_naming(pod.user) + _naming(pod.viewer)
    )

    await _read(pod, town["id"], pod.user, named["seq"])

    assert (await _unread(pod, town["id"], pod.user))["lines"] == 0
    theirs = await _unread(pod, town["id"], pod.viewer)
    assert (theirs["lines"], theirs["mentions"]) == (1, 1), theirs
    assert (await _totals(pod, pod.viewer))["channels"] >= 1


async def test_reading_to_the_end_reads_what_named_them_before_they_joined(pod: Pod):
    town = await group(pod, title="Town square")
    named = await say(pod, town["id"], "ask the user", parts=_naming(pod.user))
    joined = await pod.client(pod.user).post(f"{pod.base}/{town['id']}/members/me")
    assert joined.json()["unread"]["mentions"] == 1, joined.text

    # Their read number is already there: reading to it still reads the line.
    await _read(pod, town["id"], pod.user, named["seq"])

    assert (await _unread(pod, town["id"], pod.user))["mentions"] == 0
    assert (await _totals(pod, pod.user))["mentions"] == 0


async def test_the_member_looking_after_contacts_hears_a_new_one(
    pod: Pod, db_session: AsyncSession
):
    before = await _totals(pod, pod.editor)

    source, _ = await contact_place(db_session, pod, keeper=pod.editor)

    # In it from the contact's first word, without joining: theirs to answer.
    shown = (await pod.client(pod.editor).get(f"{pod.base}/{source.channel_id}")).json()
    assert shown["joined"] is True, shown
    assert shown["unread"]["lines"] == 1, shown
    assert (await _totals(pod, pod.editor))["channels"] == before["channels"] + 1


async def test_a_keeper_who_cannot_open_contacts_is_not_put_in_their_channel(
    pod: Pod, db_session: AsyncSession
):
    source, _ = await contact_place(db_session, pod, keeper=pod.user)

    members = await pod.client(pod.owner).get(f"{pod.base}/{source.channel_id}/members")
    assert members.status_code == 200, members.text
    assert pod.user["id"] not in {
        item["person"]["id"] for item in members.json()["items"]
    }
