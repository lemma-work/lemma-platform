"""Working with data → seeing records change while you are looking at them.

A table somebody is watching has to update itself, or every person with it open
is looking at something slightly wrong and does not know it. Three things have
to hold: changes arrive as they happen, changes the watcher may not see never
arrive at all, and a watcher who drops off can pick up where they left rather
than missing the gap or replaying the lot.

A websocket, because that is what the product offers and what "as it happens"
requires.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [
    journey("Working with data"),
    capability("Watch records change"),
]

#: Long enough that a change made straight after connecting still arrives on a
#: loaded machine, short enough that a scenario asserting nothing arrives does
#: not take all day.
ARRIVES_WITHIN = 20.0


async def _ready(socket) -> str:
    """Take the opening frame and return the cursor it hands back.

    The server announces where in the stream it started before any change is
    forwarded, so a client always holds a resume point — including for changes
    it has not seen yet.
    """
    opening = json.loads(await asyncio.wait_for(socket.recv(), timeout=ARRIVES_WITHIN))
    assert opening.get("type") == "ready", f"unexpected opening frame: {opening}"
    return str(opening.get("since") or "")


def _operation(frame: dict) -> str:
    """What a frame says happened.

    Carried twice — as `operation`, and in the event `type`
    (`datastore.record.insert`). Reading only one of them makes an assertion
    depend on which the server happened to fill in.
    """
    stated = str(frame.get("operation") or "").upper()
    return stated or str(frame.get("type") or "").rsplit(".", 1)[-1].upper()


async def _next_change(
    socket, *, was: str | None = None, timeout: float = ARRIVES_WITHIN
) -> dict:
    """The next change frame, optionally the next one of a given operation.

    Skipping past others matters: the stream is anchored at connect time and a
    write made just before that can still be in flight, so "the next frame" is
    not reliably "the frame for the thing I just did".
    """
    while True:
        frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=timeout))
        if frame.get("type") == "ready":
            continue
        if was is None or _operation(frame) == was.upper():
            return frame


@pytest.fixture
async def a_watched_table(world):
    alice = await world.person("daniel")
    pod = await alice.works_in("sales")
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    return alice, alice.organization, pod, table


@scenario("A person watching a table sees a new record arrive")
@proves("PS-DATA-060")
@covers("record.create")
async def test_a_new_record_arrives_live(a_watched_table):
    alice, _organization, pod, table = a_watched_table

    async with websockets.connect(alice.changes_url(pod)) as socket:
        await _ready(socket)

        await alice.adds_record(
            {"title": "written while watching"}, to_table=table["name"], in_pod=pod
        )

        change = await _next_change(socket)

    assert _operation(change) == "INSERT", change
    assert str(change.get("table_name")) == table["name"], change
    assert "written while watching" in json.dumps(change.get("payload") or {}), (
        f"the change arrived without saying what changed: {change}"
    )


@scenario("A watcher sees updates and deletions too, not only new rows")
@proves("PS-DATA-060")
@covers("record.update", "record.delete")
async def test_updates_and_deletions_arrive(a_watched_table):
    alice, _organization, pod, table = a_watched_table
    record = await alice.adds_record(
        {"title": "before"}, to_table=table["name"], in_pod=pod
    )

    async with websockets.connect(alice.changes_url(pod)) as socket:
        await _ready(socket)

        await alice.updates_record(
            record, data={"title": "after"}, in_table=table["name"], in_pod=pod
        )
        updated = await _next_change(socket, was="UPDATE")
        await alice.deletes_record(record, in_table=table["name"], in_pod=pod)
        deleted = await _next_change(socket, was="DELETE")

    assert _operation(updated) == "UPDATE", updated
    assert _operation(deleted) == "DELETE", deleted
    assert str(updated.get("record_id")) == str(record["id"]), updated


@scenario("A watcher is sent nothing from a pod they cannot see")
@proves("PS-DATA-060", "PS-ACCESS-001")
@covers("record.create")
async def test_a_stranger_is_sent_nothing(world, a_watched_table):
    alice, _organization, pod, table = a_watched_table
    stranger = await world.person("hannah")

    # Either refusing the handshake or accepting it and forwarding nothing is a
    # correct answer. Leaking one row is not, and a scenario that only checked
    # the handshake would miss exactly that.
    try:
        async with websockets.connect(stranger.changes_url(pod)) as socket:
            await asyncio.wait_for(socket.recv(), timeout=ARRIVES_WITHIN)
            await alice.adds_record(
                {"title": "not for you"}, to_table=table["name"], in_pod=pod
            )
            leaked = await asyncio.wait_for(socket.recv(), timeout=8.0)
    except TimeoutError:
        # Must precede the OSError handler: `TimeoutError` is a *subclass* of
        # `OSError`, so ordering it second makes it unreachable and collapses
        # the two outcomes this scenario means to tell apart.
        return  # connected, and told nothing
    except (websockets.exceptions.WebSocketException, OSError):
        return  # refused at the door, which is the stronger answer

    raise AssertionError(
        f"someone with no access to the pod was sent one of its records: {leaked}"
    )


@scenario("A watcher who drops off picks up the changes they missed")
@proves("PS-DATA-060")
@covers("record.create")
async def test_a_reconnecting_watcher_resumes(a_watched_table):
    alice, _organization, pod, table = a_watched_table

    async with websockets.connect(alice.changes_url(pod)) as socket:
        cursor = await _ready(socket)

    # Written while nobody is watching. Without a resume cursor this is the
    # change that vanishes — the one a person never learns about, because
    # reconnecting starts from "now".
    await alice.adds_record(
        {"title": "missed while away"}, to_table=table["name"], in_pod=pod
    )

    async with websockets.connect(alice.changes_url(pod, since=cursor)) as socket:
        await _ready(socket)
        caught_up = await _next_change(socket, was="INSERT")

    assert "missed while away" in json.dumps(caught_up.get("payload") or {}), (
        f"reconnecting skipped the change made while away: {caught_up}"
    )
