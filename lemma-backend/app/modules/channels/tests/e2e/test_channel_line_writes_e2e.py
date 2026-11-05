"""Writing in channels, as a client sees it come back.

* A client's name for its line is shown back to it as sent -- never to
  anybody else -- so it can match the line it drew before the answer came.
* A name, a key, is one request: sent again with something else it is
  refused, saying which it was -- for lines, groups and notices alike.
* An edit naming nothing changes nothing; a deleted last line says so.
* A thread counts its live replies, on its root wherever the root is shown,
  and is read from its newest end as well as its start.
* A group keeps an owner, and a private one somebody in it.
* A notice sender is held to a rate.

Against Postgres, through the API.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.channels.tests.e2e.channel_pod import Pod, group, say

pytestmark = pytest.mark.e2e


async def test_a_clients_name_for_its_line_comes_back_as_sent(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    lines = f"{pod.base}/{crew['id']}/messages"
    name = f"draft-{uuid4()}"

    mine = await say(pod, crew["id"], "hello", client_message_id=name)
    assert mine["client_message_id"] == name
    listed = await pod.client().get(lines)
    assert listed.json()["items"][-1]["client_message_id"] == name
    # Another reader sees the line, not the writer's name for it.
    theirs = await pod.client(pod.user).get(lines)
    assert theirs.json()["items"][-1]["client_message_id"] is None

    # A key names the line when no client_message_id does.
    key = f"key-{uuid4()}"
    keyed = await pod.client().post(
        lines, json={"text": "by key"}, headers={"Idempotency-Key": key}
    )
    assert keyed.status_code == 201, keyed.text
    assert keyed.json()["client_message_id"] == key


async def test_a_name_sent_again_with_something_else_is_refused_saying_which(
    pod: Pod,
):
    crew = await group(pod)
    lines = f"{pod.base}/{crew['id']}/messages"
    owner = pod.client()
    key = {"Idempotency-Key": f"key-{uuid4()}"}

    first = await owner.post(lines, json={"text": "one"}, headers=key)
    again = await owner.post(lines, json={"text": "one"}, headers=key)
    other = await owner.post(lines, json={"text": "two"}, headers=key)
    assert first.status_code == again.status_code == 201, again.text
    assert first.json()["id"] == again.json()["id"]
    assert other.status_code == 422, other.text
    assert "Idempotency-Key" in other.json()["message"]

    both = await owner.post(
        lines,
        json={"text": "three", "client_message_id": "one-name"},
        headers={"Idempotency-Key": "another-name"},
    )
    assert both.status_code == 422, both.text

    group_key = {"Idempotency-Key": f"group-{uuid4()}"}
    made = await owner.post(pod.base, json={"title": "Launch"}, headers=group_key)
    replayed = await owner.post(pod.base, json={"title": "Launch"}, headers=group_key)
    renamed = await owner.post(pod.base, json={"title": "Other"}, headers=group_key)
    assert made.status_code == replayed.status_code == 201, replayed.text
    assert made.json()["id"] == replayed.json()["id"]
    assert renamed.status_code == 422, renamed.text
    assert "Idempotency-Key" in renamed.json()["message"]

    await pod.client(pod.user).post(f"{pod.base}/me")
    notices = f"/pods/{pod.id}/members/{pod.user['id']}/notices"
    notice_key = {"Idempotency-Key": f"notice-{uuid4()}"}
    sent = await owner.post(notices, json={"text": "Ready"}, headers=notice_key)
    resent = await owner.post(notices, json={"text": "Ready"}, headers=notice_key)
    changed = await owner.post(notices, json={"text": "Not ready"}, headers=notice_key)
    assert sent.status_code == resent.status_code == 201, resent.text
    assert sent.json()["message_id"] == resent.json()["message_id"]
    assert changed.status_code == 422, changed.text


async def test_an_edit_naming_nothing_changes_nothing(pod: Pod):
    crew = await group(pod)
    line = await say(pod, crew["id"], "as it was")
    path = f"{pod.base}/{crew['id']}/messages/{line['id']}"

    untouched = await pod.client().patch(path, json={})
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["edited_at"] is None
    assert untouched.json()["text"] == "as it was"

    edited = await pod.client().patch(path, json={"text": "as it is"})
    assert edited.json()["edited_at"] is not None


async def test_a_deleted_last_line_says_so_in_the_list(pod: Pod):
    crew = await group(pod)
    await say(pod, crew["id"], "first")
    last = await say(pod, crew["id"], "oops")
    gone = await pod.client().delete(f"{pod.base}/{crew['id']}/messages/{last['id']}")
    assert gone.status_code == 204, gone.text

    listed = await pod.client().get(pod.base)
    row = next(item for item in listed.json()["items"] if item["id"] == crew["id"])
    assert row["last_line"]["id"] == last["id"]
    assert row["last_line"]["deleted"] is True
    assert row["last_line"]["text"] == ""


async def test_a_thread_counts_its_live_replies_and_reads_from_its_newest(pod: Pod):
    crew = await group(pod)
    root = await say(pod, crew["id"], "Plan for Tuesday")
    replies = [
        await say(pod, crew["id"], f"reply {n}", thread_root_id=root["id"])
        for n in range(5)
    ]
    thread = f"{pod.base}/{crew['id']}/messages/{root['id']}/thread"

    shown = (await pod.client().get(thread)).json()
    assert shown["root"]["reply_count"] == 5

    gone = await pod.client().delete(
        f"{pod.base}/{crew['id']}/messages/{replies[0]['id']}"
    )
    assert gone.status_code == 204, gone.text
    shown = (await pod.client().get(thread)).json()
    assert shown["root"]["reply_count"] == 4
    timeline = (await pod.client().get(f"{pod.base}/{crew['id']}/messages")).json()
    assert timeline["items"][0]["reply_count"] == 4

    newest = (
        await pod.client().get(thread, params={"newest": "true", "limit": 2})
    ).json()
    assert [item["text"] for item in newest["items"]] == ["reply 3", "reply 4"]
    assert newest["has_more"] is True
    older = (
        await pod.client().get(
            thread, params={"before_seq": newest["next_before_seq"], "limit": 2}
        )
    ).json()
    assert [item["text"] for item in older["items"]] == ["reply 1", "reply 2"]

    both = await pod.client().get(thread, params={"newest": "true", "after_seq": 1})
    assert both.status_code == 422, both.text


async def test_a_group_keeps_an_owner_and_a_private_one_somebody(pod: Pod):
    crew = await group(pod, as_user=pod.user, members=[pod.editor["id"]])
    members = f"{pod.base}/{crew['id']}/members"
    user = pod.client(pod.user)

    demoted = await user.patch(
        f"{members}/MEMBER:{pod.user['id']}", json={"role": "member"}
    )
    assert demoted.status_code == 409, demoted.text
    assert demoted.json()["code"] == "LAST_OWNER"
    removed = await pod.client(pod.editor).delete(f"{members}/MEMBER:{pod.user['id']}")
    assert removed.status_code == 409, removed.text
    assert removed.json()["code"] == "LAST_OWNER"

    # Its last owner leaving hands it to whoever has been in it longest.
    left = await user.delete(f"{members}/me")
    assert left.status_code == 204, left.text
    roster = (await pod.client(pod.editor).get(members)).json()["items"]
    assert [(m["person"]["id"], m["role"]) for m in roster] == [
        (pod.editor["id"], "owner")
    ]

    hidden = await group(pod, visibility="PRIVATE", title="Just me")
    alone = await pod.client().delete(f"{pod.base}/{hidden['id']}/members/me")
    assert alone.status_code == 409, alone.text
    assert alone.json()["code"] == "LAST_MEMBER"


async def test_a_member_may_send_only_so_many_notices_a_minute(pod: Pod):
    await pod.client(pod.user).post(f"{pod.base}/me")
    notices = f"/pods/{pod.id}/members/{pod.user['id']}/notices"
    statuses = [
        (await pod.client().post(notices, json={"text": f"n{n}"})).status_code
        for n in range(25)
    ]
    assert statuses.count(201) == 20, statuses
    assert statuses[-1] == 429, statuses
    refused = await pod.client().post(notices, json={"text": "again"})
    assert refused.headers.get("Retry-After"), refused.headers
    assert refused.json()["code"] == "TOO_MANY_NOTICES"
