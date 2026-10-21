"""What a channel's readers are given beside the lines: the files a line
shares, where one's own line went, and the contacts waiting on a person.

A file shared in a private group is read through the line, by the group's
people, as its writer reads it -- never by a pod member who cannot see the
group, whatever folder it sits in.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place, group

pytestmark = pytest.mark.e2e


async def _upload(pod: Pod, who: dict, *, name: str, content: bytes) -> str:
    uploaded = await pod.client(who).post(
        f"/pods/{pod.id}/datastore/files",
        data={"directory_path": "/me", "search_enabled": "false"},
        files={"data": (name, content, "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["path"]


async def test_a_file_in_a_private_group_is_read_by_its_people_only(pod: Pod):
    hidden = await group(
        pod,
        title="Board pack",
        visibility="PRIVATE",
        members=[pod.editor["id"]],
        as_user=pod.user,
    )
    path = await _upload(pod, pod.user, name="pack.txt", content=b"Q3 numbers")
    said = await pod.client(pod.user).post(
        f"{pod.base}/{hidden['id']}/messages",
        json={
            "text": "the pack",
            "parts": [{"type": "file", "path": path, "name": "pack.txt"}],
        },
    )
    assert said.status_code == 201, said.text
    [part] = said.json()["parts"]
    # Named by the file itself: `/me/…` would mean every reader's own.
    assert part["file_id"] and not part["path"].startswith("/me")
    assert part["size"] == len(b"Q3 numbers")
    file_url = f"{pod.base}/{hidden['id']}/messages/{said.json()['id']}/files/{part['file_id']}"

    shared = await pod.client(pod.editor).get(file_url)
    assert shared.status_code == 200, shared.text
    assert shared.content == b"Q3 numbers"
    assert shared.headers["content-disposition"].startswith("attachment;")

    # Somebody in the pod but not the group: neither through the line nor
    # through the file's own folder.
    assert (await pod.client(pod.viewer).get(file_url)).status_code == 404
    direct = await pod.client(pod.viewer).get(
        f"/pods/{pod.id}/datastore/files/download", params={"path": part["path"]}
    )
    assert direct.status_code in (403, 404), direct.text


async def test_a_line_shares_only_a_file_its_writer_may_read(pod: Pod):
    town = await group(pod, title="Town", members=[pod.user["id"]])
    theirs = await _upload(pod, pod.user, name="mine.txt", content=b"private")
    for path in (theirs, "/me/never-uploaded.txt"):
        refused = await pod.client(pod.editor).post(
            f"{pod.base}/{town['id']}/messages",
            json={
                "text": "look",
                "parts": [{"type": "file", "path": path, "name": "x.txt"}],
            },
        )
        assert refused.status_code == 422, (path, refused.text)


async def test_a_line_says_where_it_went_to_everyone_who_reads_it(
    pod: Pod, db_session: AsyncSession
):
    source, _ = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    said = await pod.client(pod.editor).post(
        f"{channel}/messages", json={"text": "On its way"}
    )
    assert said.status_code == 201, said.text
    [went] = said.json()["delivered"]
    assert went["source_id"] == str(source.id)
    assert went["status"] in {"PENDING", "SENT", "FAILED", "SKIPPED", "DEAD"}

    read_by_owner = await pod.client().get(f"{channel}/messages")
    mine = await pod.client(pod.editor).get(f"{channel}/messages")
    line = said.json()["id"]
    [as_owner] = [m for m in read_by_owner.json()["items"] if m["id"] == line]
    [as_writer] = [m for m in mine.json()["items"] if m["id"] == line]
    assert as_owner["delivered"] == as_writer["delivered"]
    assert as_writer["delivered"][0]["id"] == went["id"]


async def test_contacts_waiting_on_a_person_are_listed_by_the_server(
    pod: Pod, db_session: AsyncSession
):
    source, _ = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    editor = pod.client(pod.editor)

    async def waiting() -> list[str]:
        listed = await editor.get(pod.base, params={"needs_person": True})
        assert listed.status_code == 200, listed.text
        return [item["id"] for item in listed.json()["items"]]

    # The agent answers her: nobody is needed.
    assert await waiting() == []
    assert (
        await editor.patch(channel, json={"agents_answer": False})
    ).status_code == 200
    assert await waiting() == [str(source.channel_id)]
    taken = await editor.patch(channel, json={"assignee": "me"})
    assert taken.status_code == 200, taken.text
    assert await waiting() == []
