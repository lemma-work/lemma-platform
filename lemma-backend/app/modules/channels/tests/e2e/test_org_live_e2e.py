"""The organization's live socket -- real websocket, Postgres and Redis.

``/organizations/{id}/live`` carries one topic, ``org``: every pod of the
organization a reader may open, over one subscription. Writes go through the
REST API or the modules' contracts and reach the socket by the signals and
reads production uses. What a pod's own socket would not show a reader, this
one must not show either.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import call_for_person
from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place, group, say
from app.modules.test_support.e2e.live_socket import LiveClient, org_socket, ws_url
from app.modules.test_support.e2e.runtime import backend_server as _backend_server
from app.modules.test_support.e2e_authz import add_pod_member

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server


async def _refused(server: dict[str, str], org_id: str, token: str | None) -> int:
    """Open an organization socket that must be refused: its close code."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with connect(
        ws_url(server, f"/organizations/{org_id}/live"), additional_headers=headers
    ) as ws:
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                frame = await asyncio.wait_for(ws.recv(), 10)
                assert '"ready"' not in str(frame), frame
    assert closed.value.rcvd is not None, "closed without a close frame"
    return closed.value.rcvd.code


async def _covered(live: LiveClient) -> set[str]:
    subscribed = await live.subscribe("o", "org")
    assert subscribed["type"] == "subscribed", subscribed
    pods = await live.event("o", "pods")
    return set(pods["pod_ids"])  # type: ignore[arg-type]


async def _another_pod(pod: Pod) -> str:
    """A second pod in the organization, with only its owner in it."""
    made = await pod.scenario.owner_client.post(
        "/pods",
        json={
            "organization_id": pod.scenario.org_id,
            "name": f"Elsewhere {uuid4().hex[:8]}",
            "type": "HYBRID",
        },
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


async def _org_member_id(pod: Pod, user: dict) -> str:
    members = await pod.scenario.owner_client.get(
        f"/organizations/{pod.scenario.org_id}/members"
    )
    assert members.status_code == 200, members.text
    return next(
        str(item["id"])
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == user["email"]
    )


async def _pod_member_id(pod: Pod, user_id: str) -> str:
    found = await pod.client().get(
        f"/pods/{pod.id}/members/lookup/by-user-id/{user_id}"
    )
    assert found.status_code == 200, found.text
    return str(found.json()["pod_member_id"])


# -- who may connect ---------------------------------------------------------------


async def test_only_the_organizations_members_may_open_its_socket(
    pod: Pod, backend_server
) -> None:
    outsider = await pod.scenario.create_user("org-live-outsider")

    assert (
        await _refused(backend_server, pod.scenario.org_id, outsider["token"]) == 4403
    )
    # No such organization reads as one they are not in: REST answers 403 too.
    assert await _refused(backend_server, str(uuid4()), pod.owner["token"]) == 4403
    assert await _refused(backend_server, pod.scenario.org_id, None) == 4401
    assert await _refused(backend_server, pod.scenario.org_id, "not-a-token") == 4401


async def test_a_pods_assistant_may_not_hold_its_organizations_socket(
    pod: Pod, backend_server
) -> None:
    """Its token is pinned to one pod; the organization's socket reads them
    all, so it is refused there even though it keeps its own pod's socket."""
    headers = await pod.scenario.default_pod_agent_headers(user=pod.owner)
    token = headers["Authorization"].removeprefix("Bearer ")

    assert await _refused(backend_server, pod.scenario.org_id, token) == 4403


async def test_a_reader_covers_only_the_pods_they_may_open(
    pod: Pod, backend_server
) -> None:
    elsewhere = await _another_pod(pod)

    async with (
        org_socket(backend_server, pod.scenario.org_id, pod.user["token"]) as user,
        org_socket(backend_server, pod.scenario.org_id, pod.owner["token"]) as owner,
    ):
        assert user.ready["topics"] == ["org"]
        assert await _covered(user) == {pod.id}
        assert await _covered(owner) == {pod.id, elsewhere}


# -- what is said ------------------------------------------------------------------


async def test_a_line_reaches_an_org_reader_as_their_counts_in_its_pod(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Counts", members=[pod.editor["id"]])
    async with org_socket(
        backend_server, pod.scenario.org_id, pod.editor["token"]
    ) as editor:
        await _covered(editor)
        await say(pod, channel["id"], "news")

        moved = await editor.event(
            "o", "inbox.updated", channel_id=channel["id"], unread__lines=1
        )
        assert moved["pod_id"] == pod.id
        assert moved["totals"]["channels"] >= 1  # type: ignore[index]


async def test_a_private_group_is_never_named_to_who_is_not_in_it(
    pod: Pod, backend_server
) -> None:
    private = await group(
        pod, title="Inner", visibility="PRIVATE", members=[pod.editor["id"]]
    )
    async with org_socket(
        backend_server, pod.scenario.org_id, pod.user["token"]
    ) as user:
        await _covered(user)
        await say(pod, private["id"], "for the few")
        await user.quiet()

    assert private["id"] not in str(user.frames)


async def test_a_contact_waiting_on_a_person_reaches_who_may_see_contacts(
    pod: Pod, backend_server, db_session
) -> None:
    async with (
        org_socket(backend_server, pod.scenario.org_id, pod.editor["token"]) as editor,
        org_socket(backend_server, pod.scenario.org_id, pod.user["token"]) as user,
    ):
        await _covered(editor)
        await _covered(user)
        source, contact_id = await contact_place(db_session, pod)
        await call_for_person(
            SqlAlchemyUnitOfWork(db_session),
            channel_id=source.channel_id,
            reason="Order 1182",
        )
        await db_session.commit()

        waiting = await editor.event(
            "o", "waiting.started", channel_id=str(source.channel_id)
        )
        assert waiting == {
            "type": "waiting.started",
            "kind": "contact",
            "channel_id": str(source.channel_id),
            "contact_id": str(contact_id),
            "pod_id": pod.id,
        }

        taken = await pod.client(pod.editor).patch(
            f"{pod.base}/{source.channel_id}", json={"assignee": "me"}
        )
        assert taken.status_code == 200, taken.text
        stopped = await editor.event(
            "o", "waiting.stopped", channel_id=str(source.channel_id)
        )
        assert stopped["kind"] == "contact"
        await user.quiet()

    # The user may not see contacts: neither the wait nor its end reaches them.
    assert not user.events("o", "waiting.started")
    assert not user.events("o", "waiting.stopped")


async def test_a_table_made_reaches_who_may_read_it(pod: Pod, backend_server) -> None:
    async with org_socket(
        backend_server, pod.scenario.org_id, pod.viewer["token"]
    ) as viewer:
        await _covered(viewer)
        made = await pod.client().post(
            f"/pods/{pod.id}/datastore/tables",
            json={
                "name": "orders",
                "primary_key_column": "id",
                "columns": [
                    {"name": "id", "type": "UUID", "required": True, "auto": True},
                    {"name": "title", "type": "TEXT", "required": True},
                ],
            },
        )
        assert made.status_code == 201, made.text

        added = await viewer.event("o", "table.added", name="orders")
        assert added == {
            "type": "table.added",
            "table_id": made.json()["id"],
            "name": "orders",
            "pod_id": pod.id,
        }


# -- membership --------------------------------------------------------------------


async def test_taken_out_of_a_pod_a_reader_hears_nothing_more_of_it(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="After", members=[pod.user["id"]])
    async with org_socket(
        backend_server, pod.scenario.org_id, pod.user["token"]
    ) as user:
        await _covered(user)
        member_id = await _pod_member_id(pod, pod.user["id"])
        removed = await pod.client().delete(f"/pods/{pod.id}/members/{member_id}")
        assert removed.status_code == 204, removed.text

        assert await user.event("o", "pod.removed") == {
            "type": "pod.removed",
            "pod_id": pod.id,
        }
        told = len(user.events("o"))
        await say(pod, channel["id"], "after they left")
        await user.quiet()
        assert len(user.events("o")) == told


async def test_added_to_a_pod_a_reader_covers_it_from_then(
    pod: Pod, backend_server
) -> None:
    elsewhere = await _another_pod(pod)
    async with org_socket(
        backend_server, pod.scenario.org_id, pod.user["token"]
    ) as user:
        assert await _covered(user) == {pod.id}

        await add_pod_member(
            pod.scenario.owner_client,
            pod_id=elsewhere,
            organization_member_id=await _org_member_id(pod, pod.user),
            role="POD_VIEWER",
        )

        added = await user.event("o", "pod.added", pod_id=elsewhere)
        assert added["more"] is False
        made = await pod.scenario.owner_client.post(
            f"/pods/{elsewhere}/datastore/tables",
            json={
                "name": "leads",
                "primary_key_column": "id",
                "columns": [
                    {"name": "id", "type": "UUID", "required": True, "auto": True}
                ],
            },
        )
        assert made.status_code == 201, made.text
        await user.event("o", "table.added", pod_id=elsewhere, name="leads")


async def test_a_resumed_subscription_is_told_to_read_again(
    pod: Pod, backend_server
) -> None:
    async with org_socket(
        backend_server, pod.scenario.org_id, pod.user["token"]
    ) as user:
        subscribed = await user.subscribe("o", "org", since="anything")
        assert subscribed["type"] == "subscribed", subscribed
        resync = await user.until("resync", "o")
        assert resync["reason"] == "no_replay"
