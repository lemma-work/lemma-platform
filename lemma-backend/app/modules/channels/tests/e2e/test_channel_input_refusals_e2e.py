"""What a request may not carry into a channel is refused as the request's
fault -- a 422 in the one error shape, with a code -- never a 500 from the
database underneath.

* Text with a NUL character, which Postgres text cannot hold: a line, its
  parts, a title, a topic, a list's or a search's words, a notice.
* A line number past Postgres' ``bigint``.
* Naming an agent that is not one of the pod's.
* A field a request does not take; a platform nobody can be told on.

And each refusal says what it is: ``WRONG_CHANNEL_KIND``,
``CHANNEL_ARCHIVED``, ``LINE_NOT_FOUND`` ...

Against Postgres, through the API.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.channels.tests.e2e.channel_pod import Pod, group, say

pytestmark = pytest.mark.e2e

NUL = "before\x00after"


def _refused(response, status: int = 422, code: str = "VALIDATION_ERROR") -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code, body
    assert set(body) >= {"message", "code", "details"}, body


async def test_a_nul_character_is_refused_wherever_text_is_sent(pod: Pod):
    crew = await group(pod)
    owner = pod.client()
    lines = f"{pod.base}/{crew['id']}/messages"
    line = await say(pod, crew["id"], "fine")

    _refused(await owner.post(lines, json={"text": NUL}))
    _refused(
        await owner.post(
            lines,
            json={
                "text": "see",
                "parts": [{"type": "link", "url": "https://x.test", "label": NUL}],
            },
        )
    )
    _refused(await owner.patch(f"{lines}/{line['id']}", json={"text": NUL}))
    _refused(await owner.post(pod.base, json={"title": NUL}))
    _refused(await owner.patch(f"{pod.base}/{crew['id']}", json={"topic": NUL}))
    _refused(await owner.get(pod.base, params={"q": NUL}))
    _refused(await owner.get(f"/pods/{pod.id}/search", params={"q": NUL}))
    await pod.client(pod.user).post(f"{pod.base}/me")
    _refused(
        await owner.post(
            f"/pods/{pod.id}/members/{pod.user['id']}/notices", json={"text": NUL}
        )
    )

    listed = await owner.get(lines)
    assert [item["text"] for item in listed.json()["items"]] == ["fine"]


async def test_a_line_number_past_bigint_is_refused(pod: Pod):
    crew = await group(pod)
    owner = pod.client()
    huge = 10**23
    _refused(await owner.post(f"{pod.base}/{crew['id']}/read", json={"seq": huge}))
    for param in ("after_seq", "before_seq"):
        _refused(
            await owner.get(
                f"{pod.base}/{crew['id']}/messages", params={param: str(huge)}
            )
        )
    line = await say(pod, crew["id"], "root")
    _refused(
        await owner.get(
            f"{pod.base}/{crew['id']}/messages/{line['id']}/thread",
            params={"after_seq": str(huge)},
        )
    )
    # The largest number there is, is a number.
    read = await owner.post(f"{pod.base}/{crew['id']}/read", json={"seq": 2**63 - 1})
    assert read.status_code == 200, read.text
    assert read.json()["read_seq"] == 1


async def test_naming_an_agent_not_the_pods_is_refused_not_a_500(pod: Pod):
    crew = await group(pod)
    stranger = uuid4()
    said = await pod.client().post(
        f"{pod.base}/{crew['id']}/messages",
        json={
            "text": "@Ghost hi",
            "parts": [
                {
                    "type": "mention",
                    "kind": "AGENT",
                    "id": str(stranger),
                    "name": "Ghost",
                }
            ],
        },
    )
    _refused(said)
    assert "Ghost" in said.json()["message"]

    # The pod's own assistant -- its id is the pod's -- may be named.
    named = await pod.client().post(
        f"{pod.base}/{crew['id']}/messages",
        json={
            "text": "@Bolt hi",
            "parts": [
                {"type": "mention", "kind": "AGENT", "id": pod.id, "name": "Bolt"}
            ],
        },
    )
    assert named.status_code == 201, named.text


async def test_a_field_or_platform_a_request_does_not_take_is_refused(pod: Pod):
    crew = await group(pod)
    owner = pod.client()
    added = await owner.post(
        f"{pod.base}/{crew['id']}/members", json={"members": [pod.user["id"]]}
    )
    _refused(added)
    pigeon = await owner.patch(
        f"{pod.base}/{crew['id']}/members/me", json={"notify_via": ["CARRIER-PIGEON"]}
    )
    _refused(pigeon)
    email = await owner.patch(
        f"{pod.base}/{crew['id']}/members/me", json={"notify_via": [" email "]}
    )
    assert email.status_code == 200, email.text
    assert email.json()["me"]["notify_via"] == ["EMAIL"]


async def test_each_refusal_names_what_it_is(pod: Pod):
    crew = await group(pod)
    owner = pod.client()
    await owner.post(f"{pod.base}/me")
    mine = (await owner.get(f"{pod.base}/me")).json()

    _refused(
        await owner.delete(f"{pod.base}/{mine['id']}/members/me"),
        409,
        "WRONG_CHANNEL_KIND",
    )
    _refused(await owner.get(f"{pod.base}/{uuid4()}"), 404, "CHANNEL_NOT_FOUND")
    _refused(
        await owner.get(f"{pod.base}/{crew['id']}/messages/{uuid4()}/thread"),
        404,
        "LINE_NOT_FOUND",
    )
    _refused(
        await owner.get(pod.base, params={"cursor": "not-ours"}), 400, "INVALID_CURSOR"
    )
    archived = await owner.patch(f"{pod.base}/{crew['id']}", json={"archived": True})
    assert archived.status_code == 200, archived.text
    _refused(
        await owner.post(f"{pod.base}/{crew['id']}/messages", json={"text": "hi"}),
        409,
        "CHANNEL_ARCHIVED",
    )
    _refused(
        await pod.client(pod.user).post(
            f"{pod.base}/{crew['id']}/members", json={"people": [pod.user["id"]]}
        ),
        403,
        "INSUFFICIENT_PERMISSION",
    )
