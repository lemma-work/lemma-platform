"""Who may do what in a pod's channels, and what reading never does -- end to end.

``channel.read`` / ``channel.write`` / ``channel.manage`` by role (viewer,
user, editor+), with ``contact.read`` / ``contact.message`` on top for a
contact's channel (``api.access`` tables it). A member's own channel is made
when they join the pod, never by a GET; ``POST /channels/me`` makes it for a
member from before. Creates replay by ``Idempotency-Key``. The routes a bot's
groups moved to win over ``/channels/{channel_id}``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.channels.contracts import NewLine, Principal, Sender, post
from app.modules.channels.events.member_joined_work import (
    register_member_joined_work,
)
from app.modules.channels.events.member_left_work import register_member_left_work
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
)
from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place, group, say
from app.modules.test_support.e2e_authz import add_pod_member, invite_org_member

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _members_joined() -> int:
    """Run channels' work on the pod's members joining and leaving, and
    nothing else."""
    registry = WorkRegistry()
    register_member_joined_work(registry)
    register_member_left_work(registry)
    return await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )


async def _rows(db_session: AsyncSession, pod: Pod) -> tuple[int, int]:
    pod_id = UUID(pod.id)
    channels = await db_session.scalar(
        select(func.count()).where(ChannelModel.pod_id == pod_id)
    )
    seats = await db_session.scalar(
        select(func.count()).where(ChannelMemberModel.pod_id == pod_id)
    )
    return int(channels or 0), int(seats or 0)


# -- the permission matrix ------------------------------------------------------


async def test_a_viewer_reads_and_answers_but_does_not_write(pod: Pod):
    town = await group(pod, title="Town square")
    await say(pod, town["id"], "morning")
    viewer = pod.client(pod.viewer)

    assert (await viewer.get(pod.base)).status_code == 200
    lines = await viewer.get(f"{pod.base}/{town['id']}/messages")
    assert [m["text"] for m in lines.json()["items"]] == ["morning"]
    assert (await viewer.post(f"{pod.base}/me")).status_code == 201
    joined = await viewer.post(f"{pod.base}/{town['id']}/members/me")
    assert joined.status_code == 200, joined.text

    for refused in (
        await viewer.post(f"{pod.base}/{town['id']}/messages", json={"text": "hi"}),
        await viewer.post(pod.base, json={"title": "mine"}),
        await viewer.patch(f"{pod.base}/{town['id']}", json={"topic": "x"}),
    ):
        assert refused.status_code == 403, refused.text


async def test_a_user_writes_and_keeps_their_own_group_but_not_anothers(pod: Pod):
    theirs = await group(pod, title="Owner's")
    user = pod.client(pod.user)

    said = await user.post(f"{pod.base}/{theirs['id']}/messages", json={"text": "hi"})
    assert said.status_code == 201, said.text
    for refused in (
        await user.patch(f"{pod.base}/{theirs['id']}", json={"title": "Mine now"}),
        await user.patch(f"{pod.base}/{theirs['id']}", json={"archived": True}),
        await user.post(
            f"{pod.base}/{theirs['id']}/members", json={"people": [pod.viewer["id"]]}
        ),
        await user.delete(f"{pod.base}/{theirs['id']}"),
    ):
        assert refused.status_code == 403, refused.text

    own = await group(pod, title="User's", as_user=pod.user)
    assert own["me"]["can_manage"] is True
    renamed = await user.patch(f"{pod.base}/{own['id']}", json={"title": "Renamed"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Renamed"


async def test_an_editor_manages_any_group(pod: Pod):
    theirs = await group(pod, title="Owner's")
    editor = pod.client(pod.editor)

    shown = await editor.get(f"{pod.base}/{theirs['id']}")
    assert shown.json()["me"]["can_manage"] is True
    renamed = await editor.patch(f"{pod.base}/{theirs['id']}", json={"title": "Ed's"})
    assert renamed.status_code == 200, renamed.text
    added = await editor.post(
        f"{pod.base}/{theirs['id']}/members", json={"people": [pod.viewer["id"]]}
    )
    assert added.status_code == 200, added.text
    assert pod.viewer["id"] in {m["person"]["id"] for m in added.json()["members"]}
    removed = await editor.delete(
        f"{pod.base}/{theirs['id']}/members/MEMBER:{pod.viewer['id']}"
    )
    assert removed.status_code == 204, removed.text
    assert (await editor.delete(f"{pod.base}/{theirs['id']}")).status_code == 204


async def test_a_private_group_is_joined_on_request_only_by_who_manages_channels(
    pod: Pod,
):
    """Nobody learns a private group from a join; an editor, who manages the
    pod's channels, may ask in -- and then reads it like any member."""
    hidden = await group(pod, title="Quiet", visibility="PRIVATE", as_user=pod.user)
    await say(pod, hidden["id"], "Not for everyone", as_user=pod.user)
    join = f"{pod.base}/{hidden['id']}/members/me"

    assert (await pod.client(pod.viewer).post(join)).status_code == 404
    editor = pod.client(pod.editor)
    assert (await editor.get(f"{pod.base}/{hidden['id']}")).status_code == 404
    joined = await editor.post(join)
    assert joined.status_code == 200, joined.text
    assert joined.json()["joined"] is True
    lines = await editor.get(f"{pod.base}/{hidden['id']}/messages")
    assert lines.status_code == 200, lines.text
    assert [line["text"] for line in lines.json()["items"]] == ["Not for everyone"]


async def test_a_contacts_channel_takes_contact_read_to_see_and_message_to_write(
    pod: Pod, db_session: AsyncSession
):
    """A custom role that reads contacts and writes in channels still may not
    write to a contact: that is ``contact.message``."""
    source, _ = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    made = await pod.client().post(
        f"/pods/{pod.id}/roles",
        json={
            "name": "CONTACT_WATCHER",
            "permission_ids": ["channel.write", "contact.read"],
        },
    )
    assert made.status_code == 201, made.text
    watcher = await pod.scenario.create_user("chv2-watcher")
    await pod.scenario.add_user_to_pod(
        user=watcher, role="POD_USER", roles=["POD_USER", made.json()["name"]]
    )

    assert (await pod.client(pod.user).get(channel)).status_code == 404
    watching = pod.client(watcher)
    assert (await watching.get(f"{channel}/messages")).status_code == 200
    for refused in (
        await watching.post(f"{channel}/messages", json={"text": "hello Dana"}),
        await watching.patch(channel, json={"status": "closed"}),
    ):
        assert refused.status_code == 403, refused.text

    editor = pod.client(pod.editor)
    said = await editor.post(f"{channel}/messages", json={"text": "hello Dana"})
    assert said.status_code == 201, said.text


# -- removing somebody else's line -------------------------------------------------


async def test_who_may_change_a_channel_removes_anybodys_line_but_never_edits_it(
    pod: Pod,
):
    town = await group(pod, title="Town square")
    by_user = await say(pod, town["id"], "buy cheap pills", as_user=pod.user)
    by_editor = await say(pod, town["id"], "welcome all", as_user=pod.editor)
    own = await say(pod, town["id"], "my own slip", as_user=pod.user)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        by_agent = await post(
            uow,
            channel_id=UUID(town["id"]),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id), on_behalf_of=Principal.member(UUID(pod.user["id"]))
                ),
                text="spam, spam",
            ),
        )
        await uow.commit()
    assert by_agent is not None
    lines = f"{pod.base}/{town['id']}/messages"
    user, editor = pod.client(pod.user), pod.client(pod.editor)

    refused = await user.delete(f"{lines}/{by_editor['id']}")
    assert refused.status_code == 403, refused.text
    rewritten = await editor.patch(f"{lines}/{by_user['id']}", json={"text": "fixed"})
    assert rewritten.status_code == 403, rewritten.text

    for line_id in (by_user["id"], by_agent.id):
        removed = await editor.delete(f"{lines}/{line_id}")
        assert removed.status_code == 204, removed.text
    assert (await user.delete(f"{lines}/{own['id']}")).status_code == 204

    read = await user.get(lines)
    assert read.status_code == 200, read.text
    shown = {line["id"]: line for line in read.json()["items"]}
    for line_id in (by_user["id"], str(by_agent.id)):
        assert shown[line_id]["deleted"] is True
        assert shown[line_id]["text"] is None
        assert shown[line_id]["removed_by"]["id"] == pod.editor["id"]
        assert shown[line_id]["removed_by"]["name"]
    assert shown[own["id"]]["deleted"] is True
    assert shown[own["id"]]["removed_by"] is None, "one's own delete is not a removal"
    assert shown[by_editor["id"]]["text"] == "welcome all"

    # A member who owns their group moderates it too.
    theirs = await group(pod, title="User's corner", as_user=pod.user)
    stray = await say(pod, theirs["id"], "off topic", as_user=pod.editor)
    taken = await user.delete(f"{pod.base}/{theirs['id']}/messages/{stray['id']}")
    assert taken.status_code == 204, taken.text


async def test_a_contacts_line_is_removed_by_who_writes_to_contacts(
    pod: Pod, db_session: AsyncSession
):
    source, _ = await contact_place(db_session, pod)
    lines = f"{pod.base}/{source.channel_id}/messages"
    editor = pod.client(pod.editor)
    [said] = (await editor.get(lines)).json()["items"]

    removed = await editor.delete(f"{lines}/{said['id']}")

    assert removed.status_code == 204, removed.text
    [after] = (await editor.get(lines)).json()["items"]
    assert after["deleted"] is True
    assert after["removed_by"]["id"] == pod.editor["id"]


# -- PATCH -------------------------------------------------------------------------


async def test_a_contact_is_assigned_to_me(pod: Pod, db_session: AsyncSession):
    source, _ = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    editor = pod.client(pod.editor)

    mine = await editor.patch(channel, json={"assignee": "me"})
    assert mine.status_code == 200, mine.text
    assert mine.json()["assignee"]["id"] == pod.editor["id"]
    listed = await editor.get(pod.base, params={"assignee": "me"})
    assert [c["id"] for c in listed.json()["items"]] == [str(source.channel_id)]
    untouched = await editor.patch(channel, json={"status": "waiting_on_contact"})
    assert untouched.json()["assignee"]["id"] == pod.editor["id"]
    cleared = await editor.patch(channel, json={"assignee": None})
    assert cleared.json()["assignee"] is None
    junk = await editor.patch(channel, json={"assignee": "somebody"})
    assert junk.status_code == 422, junk.text


async def test_an_edit_changes_what_it_names_and_null_clears_the_parts(pod: Pod):
    town = await group(pod, title="Town square", members=[pod.editor["id"]])
    mention = {"type": "mention", "kind": "MEMBER", "id": pod.editor["id"], "name": "E"}
    line = await say(pod, town["id"], "@E look", parts=[mention])
    path = f"{pod.base}/{town['id']}/messages/{line['id']}"
    owner = pod.client()

    retexted = await owner.patch(path, json={"text": "@E look again"})
    assert retexted.status_code == 200, retexted.text
    assert retexted.json()["text"] == "@E look again"
    assert [p["id"] for p in retexted.json()["parts"]] == [pod.editor["id"]]
    unnamed = await owner.patch(path, json={"parts": None})
    assert unnamed.status_code == 200, unnamed.text
    assert unnamed.json()["parts"] == []
    assert unnamed.json()["text"] == "@E look again"
    refused = await owner.patch(path, json={"text": None})
    assert refused.status_code == 422, refused.text


# -- a member's own channel ----------------------------------------------------------


async def test_joining_the_pod_makes_the_members_own_channel(pod: Pod):
    newcomer = await pod.scenario.create_user("chv2-newcomer")
    await pod.scenario.add_user_to_pod(user=newcomer, role="POD_USER")
    client = pod.client(newcomer)
    assert (await client.get(f"{pod.base}/me")).status_code == 404

    await _members_joined()

    mine = await client.get(f"{pod.base}/me")
    assert mine.status_code == 200, mine.text
    assert mine.json()["kind"] == "DIRECT"
    assert mine.json()["person"]["id"] == newcomer["id"]
    assert mine.json()["me"]["joined"] is True
    again = await client.post(f"{pod.base}/me")
    assert again.status_code == 200 and again.json()["id"] == mine.json()["id"]


async def test_coming_back_to_the_pod_brings_the_channel_back(pod: Pod):
    scenario = pod.scenario
    person = await scenario.create_user("chv2-returner")
    org_member = await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=person,
    )

    async def joins() -> dict:
        added = await add_pod_member(
            scenario.owner_client,
            pod_id=pod.id,
            organization_member_id=org_member["id"],
            role="POD_USER",
        )
        await _members_joined()
        return added

    seat = await joins()
    client = pod.client(person)
    mine = (await client.get(f"{pod.base}/me")).json()
    left = await scenario.owner_client.delete(
        f"/pods/{pod.id}/members/{seat['pod_member_id']}"
    )
    assert left.status_code == 204, left.text
    await _members_joined()  # the leaving runs too: it archives the channel

    await joins()

    back = await client.get(f"{pod.base}/me")
    assert back.status_code == 200, back.text
    assert back.json()["id"] == mine["id"] and back.json()["archived"] is False


async def test_reading_writes_nothing(pod: Pod, db_session: AsyncSession):
    town = await group(pod, title="Town square")
    await say(pod, town["id"], "morning")
    before = await _rows(db_session, pod)

    for who in (None, pod.editor, pod.user, pod.viewer):
        client = pod.client(who)
        for path, params in (
            (pod.base, {}),
            (pod.base, {"kind": "DIRECT"}),
            (f"{pod.base}/me", {}),
            (f"{pod.base}/{town['id']}", {}),
            (f"{pod.base}/{town['id']}/messages", {}),
            (f"/pods/{pod.id}/inbox", {}),
            (f"/pods/{pod.id}/search", {"q": "morning"}),
        ):
            read = await client.get(path, params=params)
            assert read.status_code in (200, 404), (path, read.text)

    db_session.expire_all()
    assert await _rows(db_session, pod) == before


# -- Idempotency-Key -----------------------------------------------------------------


async def test_a_notice_retried_with_its_key_is_one_line(pod: Pod):
    await pod.client(pod.user).post(f"{pod.base}/me")
    key = {"Idempotency-Key": f"notice-{uuid4().hex}"}
    sent = [
        await pod.client().post(
            f"/pods/{pod.id}/members/{pod.user['email']}/notices",
            json={"text": "Your report is ready"},
            headers=key,
        )
        for _ in range(2)
    ]
    assert [r.status_code for r in sent] == [201, 201], sent[1].text
    assert sent[0].json()["message_id"] == sent[1].json()["message_id"]
    other = await pod.client(pod.editor).post(
        f"/pods/{pod.id}/members/{pod.user['email']}/notices",
        json={"text": "Your report is ready"},
        headers=key,
    )
    assert other.json()["message_id"] != sent[0].json()["message_id"]


async def test_two_members_keys_never_name_one_line(pod: Pod):
    town = await group(pod, title="Town square")
    path = f"{pod.base}/{town['id']}/messages"
    key = {"Idempotency-Key": "same-key"}
    mine = await pod.client().post(path, json={"text": "owner's"}, headers=key)
    theirs = await pod.client(pod.user).post(path, json={"text": "user's"}, headers=key)
    assert mine.status_code == theirs.status_code == 201, theirs.text
    assert mine.json()["id"] != theirs.json()["id"]

    named = await pod.client().post(
        path, json={"text": "a", "client_message_id": "shared"}
    )
    taken = await pod.client(pod.user).post(
        path, json={"text": "b", "client_message_id": "shared"}
    )

    # A client's name is its own: the same one from two members is two lines.
    assert named.status_code == taken.status_code == 201, taken.text
    assert named.json()["id"] != taken.json()["id"]


# -- where the bot's group routes are --------------------------------------------------


async def test_the_group_routes_win_over_a_channel_id(pod: Pod):
    owner = pod.client()
    for path in (f"{pod.base}/whatsapp-groups", f"{pod.base}/telegram-links"):
        unknown = await owner.post(
            path, json={"surface_name": "no-such-bot", "title": "Acme"}
        )
        # The bot's route answered -- no bot of that name -- not a channel's.
        assert unknown.status_code == 404, unknown.text
        assert "channel" not in unknown.text.lower()
    nowhere = await owner.get(f"{pod.base}/{uuid4()}/sources/{uuid4()}")
    assert nowhere.status_code == 404, nowhere.text
    assert (await owner.get(f"{pod.base}/me")).status_code in (200, 404)
    gone = await owner.get(f"/pods/{pod.id}/sources", params={"surface_name": "x"})
    assert gone.status_code == 404, gone.text


def _paths_in_order(routes) -> list[str]:
    """Every route path, in the order a request is matched against them --
    into each included router (FastAPI keeps it on ``original_router``)."""
    found: list[str] = []
    for route in routes:
        if path := getattr(route, "path", None):
            found.append(path)
        inner = getattr(route, "original_router", None)
        found += _paths_in_order(getattr(inner, "routes", ()) or ())
    return found


def test_literal_channel_routes_are_registered_before_the_channel_id():
    from app.app import app

    order = _paths_in_order(app.routes)
    by_id = order.index("/pods/{pod_id}/channels/{channel_id}")
    for literal in ("whatsapp-groups", "telegram-links", "me"):
        assert order.index(f"/pods/{{pod_id}}/channels/{literal}") < by_id, literal
