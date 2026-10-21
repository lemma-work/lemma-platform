"""The channels live socket under faults and lost access -- real websocket,
Postgres and Redis.

The hub reads what changed and re-checks who may see it; both reads can fail
part way (the database away for a moment), and a permission change can reach
it late or not at all (a lost epoch bump). Each test injects one of those into
the server under test, which runs in this process, and holds what the socket
must still do: send everything it missed, and end what a reader lost the right
to -- within one re-check.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError
from websockets.exceptions import ConnectionClosed

from app.core.authorization.authz_epoch import pod_authz_epoch, pod_grants_epoch
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live import socket as live_socket_module
from app.core.live.config import live_settings
from app.modules.channels.contracts import NewLine, Sender, post
from app.modules.channels.infrastructure.inbox import InboxReads
from app.modules.channels.services.live import timeline
from app.modules.channels.services.live.hubs import channels_hubs
from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place, group, say
from app.modules.test_support.e2e.live_socket import LiveClient, live_socket
from app.modules.test_support.e2e.runtime import backend_server as _backend_server
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server


def _blip() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("server closed the connection"))


def _fails_once(monkeypatch, owner: object, name: str) -> list[int]:
    """Make ``owner.name`` raise a database error on its first call only."""
    real = getattr(owner, name)
    calls: list[int] = []

    async def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _blip()
        return await real(*args, **kwargs)

    monkeypatch.setattr(owner, name, flaky)
    return calls


async def _member_id(pod: Pod, user_id: str) -> str:
    found = await pod.client().get(
        f"/pods/{pod.id}/members/lookup/by-user-id/{user_id}"
    )
    assert found.status_code == 200, found.text
    return str(found.json()["pod_member_id"])


async def _set_roles(pod: Pod, user_id: str, *roles: str) -> None:
    member_id = await _member_id(pod, user_id)
    changed = await pod.client().patch(
        f"/pods/{pod.id}/members/{member_id}/roles", json={"roles": list(roles)}
    )
    assert changed.status_code == 200, changed.text


async def _remove(pod: Pod, user_id: str) -> None:
    member_id = await _member_id(pod, user_id)
    removed = await pod.client().delete(f"/pods/{pod.id}/members/{member_id}")
    assert removed.status_code == 204, removed.text


async def _contact_says(db_session, pod: Pod, source, contact_id: UUID, text: str):
    line = await post(
        SqlAlchemyUnitOfWork(db_session),
        channel_id=source.channel_id,
        line=NewLine(sender=Sender.contact(contact_id), text=text, source_id=source.id),
    )
    await db_session.commit()
    assert line is not None
    return line


def _texts(live: LiveClient) -> str:
    return str(live.frames)


# -- a read that fails part way is made again ----------------------------------


async def test_a_timeline_read_failing_mid_drain_sends_the_line_on_the_next(
    pod: Pod, backend_server, monkeypatch
) -> None:
    """The hub's cursor for the channel moves only once the line was offered:
    a read that fails after the head was read leaves it, and the retry sends
    the line instead of taking itself to be past it."""
    channel = await group(pod, title="Flaky timeline")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        assert (await live.subscribe("c", "channel", channel_id=channel["id"]))[
            "type"
        ] == "subscribed"
        calls = _fails_once(monkeypatch, timeline, "lines_view")
        line = await say(pod, channel["id"], "said while the database blinked")
        created = await live.event("c", "line.created", message__id=line["id"])
        assert created["message"]["text"] == "said while the database blinked"
        assert len(calls) >= 2, "the read never failed: nothing was tested"


async def test_an_inbox_read_failing_part_way_tells_the_reader_on_the_next(
    pod: Pod, backend_server, monkeypatch
) -> None:
    channel = await group(pod, title="Flaky inbox", members=[pod.editor["id"]])
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as live:
        assert (await live.subscribe("i", "inbox"))["type"] == "subscribed"
        calls = _fails_once(monkeypatch, InboxReads, "totals")
        await say(pod, channel["id"], "counted on the second read")
        await live.event(
            "i", "inbox.updated", channel_id=channel["id"], unread__lines=1
        )
        assert len(calls) >= 2, "the read never failed: nothing was tested"


# -- access lost, on the move and on the clock ---------------------------------


async def test_a_member_taken_out_of_the_pod_stops_hearing_a_public_group(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Everyone")
    async with live_socket(backend_server, pod.id, pod.user["token"]) as live:
        assert (await live.subscribe("c", "channel", channel_id=channel["id"]))[
            "type"
        ] == "subscribed"
        assert (await live.subscribe("i", "inbox"))["type"] == "subscribed"
        await _remove(pod, pod.user["id"])
        ended = {
            frame["id"]: frame["reason"]
            for frame in [
                await live.until("unsubscribed", "c"),
                await live.until("unsubscribed", "i"),
            ]
        }
        assert ended == {"c": "access_revoked", "i": "access_revoked"}
        await say(pod, channel["id"], "after they left the pod")
        await live.quiet(1.5)
        assert "after they left the pod" not in _texts(live)


async def _closed_with(live: LiveClient, *, timeout: float = 20) -> int | None:
    """The code the server closes the socket with; frames before it are kept."""

    async def _until_closed() -> int | None:
        try:
            while True:
                await live.recv(timeout)
        except ConnectionClosed as closed:
            return closed.rcvd.code if closed.rcvd is not None else None

    return await asyncio.wait_for(_until_closed(), timeout)


async def test_taken_out_of_the_pod_a_socket_is_closed_on_its_next_presence_call(
    pod: Pod, backend_server
) -> None:
    async with (
        live_socket(backend_server, pod.id, pod.user["token"]) as live,
        live_socket(backend_server, pod.id, pod.owner["token"]) as owner,
    ):
        assert (await live.call("presence"))["type"] == "result"
        await _remove(pod, pod.user["id"])
        await live.send(op="call", id="p", method="presence", params={})
        assert await _closed_with(live) == 4403
        assert not [frame for frame in live.frames if frame.get("id") == "p"]
        # The slot and presence are let go as the socket's handler ends,
        # just after the close frame is sent.
        for _ in range(20):
            online = (await owner.call("presence"))["result"]["online"]
            if pod.user["id"] not in online:
                break
            await owner.quiet(0.25)
        assert pod.user["id"] not in online


async def test_taken_out_of_the_pod_an_idle_socket_is_closed_on_its_refresh(
    pod: Pod, backend_server, monkeypatch
) -> None:
    """Saying nothing keeps nothing: the socket's slot refresh asks again."""
    monkeypatch.setattr(live_socket_module, "SLOT_REFRESH_SECONDS", 0.5)
    async with live_socket(backend_server, pod.id, pod.user["token"]) as live:
        await _remove(pod, pod.user["id"])
        assert await _closed_with(live) == 4403


async def test_losing_contact_read_ends_a_contacts_channel_subscription(
    pod: Pod, db_session, backend_server
) -> None:
    source, contact_id = await contact_place(db_session, pod)
    channel_id = str(source.channel_id)
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as live:
        assert (await live.subscribe("c", "channel", channel_id=channel_id))[
            "type"
        ] == "subscribed"
        assert (await live.subscribe("i", "inbox"))["type"] == "subscribed"
        await _set_roles(pod, pod.editor["id"], "POD_USER")
        ended = await live.until("unsubscribed", "c")
        assert ended["reason"] == "access_revoked"
        # Still a member who reads channels: their counts are refetched.
        refetch = await live.until("resync", "i")
        assert refetch["reason"] == "permission_changed"
        await _contact_says(db_session, pod, source, contact_id, "secret order")
        await live.quiet(1.5)
        assert "secret order" not in _texts(live)


async def test_a_recheck_that_fails_once_still_ends_the_subscription(
    pod: Pod, db_session, backend_server, monkeypatch
) -> None:
    """The epoch is taken as seen only once the re-check went through; one
    that fails is made again, not skipped until the next permission change."""
    source, _contact = await contact_place(db_session, pod)
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as live:
        assert (
            await live.subscribe("c", "channel", channel_id=str(source.channel_id))
        )["type"] == "subscribed"
        calls = _fails_once(monkeypatch, timeline, "caller_for")
        await _set_roles(pod, pod.editor["id"], "POD_USER")
        ended = await live.until("unsubscribed", "c", timeout=30)
        assert ended["reason"] == "access_revoked"
        assert len(calls) >= 2, "the re-check never failed: nothing was tested"


async def test_a_lost_epoch_bump_is_caught_by_the_recheck_on_the_clock(
    pod: Pod, db_session, backend_server, monkeypatch
) -> None:
    """A bump lost to Redis is an epoch the hub never sees move. The hub is
    given exactly that -- an epoch that never moves, through the reader it is
    built with -- and must still end the subscription on its clock."""
    monkeypatch.setattr(live_settings, "live_authz_recheck_seconds", 1.0)
    source, _contact = await contact_place(db_session, pod)

    async def never_moves(_pod_id: UUID) -> str:
        return "the epoch before the change"

    hub = channels_hubs().hub(UUID(pod.id))
    hub._epoch_of = never_moves
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as live:
        assert (
            await live.subscribe("c", "channel", channel_id=str(source.channel_id))
        )["type"] == "subscribed"
        assert channels_hubs().hub(UUID(pod.id)) is hub
        await _set_roles(pod, pod.editor["id"], "POD_USER")
        ended = await live.until("unsubscribed", "c", timeout=15)
        assert ended["reason"] == "access_revoked"


# -- what does not concern a reader does not disturb them ------------------------


async def test_a_file_upload_does_not_resync_the_inbox(
    pod: Pod, backend_server
) -> None:
    pod_id = UUID(pod.id)
    grants, whole = await pod_grants_epoch(pod_id), await pod_authz_epoch(pod_id)
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as live:
        assert (await live.subscribe("i", "inbox"))["type"] == "subscribed"
        uploaded = await pod.client().post(
            f"/pods/{pod.id}/datastore/files",
            data={"directory_path": "/", "search_enabled": "true"},
            files={"data": ("notes.md", b"# Notes\n\nnothing secret", "text/markdown")},
        )
        assert uploaded.status_code == 201, uploaded.text
        # Longer than a hub's epoch check: a resync would have come by now.
        await live.quiet(live_settings.live_authz_check_seconds + 2)
        assert [f for f in live.frames if f["type"] == "resync"] == []
    # What a search may reach moved; who may read what did not.
    assert await pod_authz_epoch(pod_id) != whole
    assert await pod_grants_epoch(pod_id) == grants


async def test_a_cursor_ahead_of_the_channel_answers_resync(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Ahead")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe(
            "c", "channel", channel_id=channel["id"], since="999999:999"
        )
        resync = await live.until("resync", "c")
        assert resync["reason"] == "cursor_expired"


# -- the session ending ----------------------------------------------------------


async def test_signing_out_closes_the_live_sockets_of_that_session(
    pod: Pod, backend_server
) -> None:
    person = await pod.scenario.create_user("live-signout")
    await pod.scenario.add_user_to_pod(user=person, role="POD_USER")
    async with live_socket(backend_server, pod.id, person["token"]) as live:
        assert (await live.subscribe("i", "inbox"))["type"] == "subscribed"
        signed_out = await pod.scenario.async_client.post(
            "/st/auth/signout", headers=auth_headers(person)
        )
        assert signed_out.status_code == 200, signed_out.text
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                await live.recv(timeout=15)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401


async def test_a_session_revoked_elsewhere_closes_the_socket(
    pod: Pod, backend_server
) -> None:
    """Revoking every session of a user -- what a password reset does --
    reaches the sockets they hold open, whichever process holds them."""
    from supertokens_python.recipe.session.asyncio import (
        revoke_all_sessions_for_user,
    )

    person = await pod.scenario.create_user("live-revoked")
    await pod.scenario.add_user_to_pod(user=person, role="POD_USER")
    async with live_socket(backend_server, pod.id, person["token"]) as live:
        assert (await live.subscribe("i", "inbox"))["type"] == "subscribed"
        await revoke_all_sessions_for_user(person["id"])
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(_drain_until_closed(live), 15)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401


async def _drain_until_closed(live: LiveClient) -> None:
    while True:
        await live.recv(timeout=15)
