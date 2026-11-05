"""Finding things across a pod's channels: search, the channel list, members.

Search is Postgres full text over the lines a reader may see and read in
full, newest first, each hit with its rank and the words around the match;
it narrows by channel, kind, who said it and when, and reaches archived
channels when asked. A line withheld from the reader is left out by the query
itself, so it never leaves a page short. The channel list narrows by
visibility, and by every word of its name -- its title, or whose own channel
it is; a channel's members are read a page at a time.

Against Postgres, through the API the four members of ``channel_pod`` use.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.channels.contracts import NewLine, Principal, Sender, post
from app.modules.channels.tests.e2e.channel_pod import (
    Pod,
    contact_place,
    group,
    keep_answers_to_the_asker,
    say,
)

pytestmark = pytest.mark.e2e


def _texts(found) -> list[str]:
    assert found.status_code == 200, found.text
    return [hit["message"]["text"] for hit in found.json()["items"]]


async def test_each_hit_carries_its_rank_and_the_words_around_it(pod: Pod):
    crew = await group(pod)
    long = (
        "We met on Tuesday and went through everything on the list, then "
        "agreed the budget for the spring launch, then talked about hiring "
        "for most of the afternoon and left the rest for next week."
    )
    await say(pod, crew["id"], long)

    found = await pod.client().get(f"/pods/{pod.id}/search", params={"q": "budget"})

    [hit] = found.json()["items"]
    assert hit["rank"] > 0
    assert "budget" in hit["snippet"]
    assert len(hit["snippet"]) < len(long)
    assert "<" not in hit["snippet"], "a snippet is plain text, never markup"


async def test_words_are_found_by_their_stem_and_as_they_are_typed(pod: Pod):
    crew = await group(pod)
    await say(pod, crew["id"], "Running the alpha build tonight")
    await say(pod, crew["id"], "The beta is parked")
    search = f"/pods/{pod.id}/search"
    owner = pod.client()

    async def found(q: str) -> list[str]:
        return _texts(await owner.get(search, params={"q": q}))

    alpha = ["Running the alpha build tonight"]
    assert await found("run") == alpha, "a stem finds the word it came from"
    assert await found("runs") == alpha
    assert await found("alph") == alpha, "a word is found as it is typed"
    assert await found("runn") == alpha, "a part-typed word past its stem"
    assert await found("alpha BUILD") == alpha
    assert await found("alpha beta") == [], "a line has every word"
    assert await found("the") == [
        "The beta is parked",
        "Running the alpha build tonight",
    ]
    # Query syntax a reader types is only words, never an error.
    assert await found("alpha & !(beta | 'x') :* <->") == []
    assert await found("al:* | park") == []
    assert await found("&|!") == []
    [hit] = (await owner.get(search, params={"q": "alph"})).json()["items"]
    assert hit["snippet"].startswith("Running the alpha")


async def test_a_withheld_answer_never_leaves_a_search_page_short(pod: Pod):
    """The newest match is an answer made with the owner's own access: the
    editor's first page of one is the line they may read, not an empty page."""
    crew = await group(pod, members=[pod.editor["id"]])
    await keep_answers_to_the_asker(pod, crew["id"])
    await say(pod, crew["id"], "plan A: rent the hall")
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await post(
            uow,
            channel_id=UUID(crew["id"]),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id), on_behalf_of=Principal.member(UUID(pod.owner["id"]))
                ),
                text="plan B: from the owner's private notes",
            ),
        )
        await uow.commit()
    editor = pod.client(pod.editor)

    first = await editor.get(f"/pods/{pod.id}/search", params={"q": "plan", "limit": 1})

    assert _texts(first) == ["plan A: rent the hall"]
    owners = await pod.client().get(f"/pods/{pod.id}/search", params={"q": "plan"})
    assert len(_texts(owners)) == 2


async def test_search_narrows_by_who_kind_and_when_and_reaches_the_archive(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    await say(pod, crew["id"], "invoice from the owner")
    await say(pod, crew["id"], "invoice from the editor", as_user=pod.editor)
    search = f"/pods/{pod.id}/search"
    owner = pod.client()

    by_editor = await owner.get(
        search,
        params={"q": "invoice", "sender_kind": "MEMBER", "sender_id": pod.editor["id"]},
    )
    assert _texts(by_editor) == ["invoice from the editor"]
    half = await owner.get(search, params={"q": "invoice", "sender_kind": "MEMBER"})
    assert half.status_code == 422, half.text
    direct_only = await owner.get(search, params={"q": "invoice", "kind": "DIRECT"})
    assert _texts(direct_only) == []
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert (
        _texts(await owner.get(search, params={"q": "invoice", "since": tomorrow}))
        == []
    )
    assert (
        len(_texts(await owner.get(search, params={"q": "invoice", "until": tomorrow})))
        == 2
    )

    archived = await owner.patch(f"{pod.base}/{crew['id']}", json={"archived": True})
    assert archived.status_code == 200, archived.text
    assert _texts(await owner.get(search, params={"q": "invoice"})) == []
    assert (
        len(
            _texts(await owner.get(search, params={"q": "invoice", "archived": "true"}))
        )
        == 2
    )


async def test_the_channel_list_narrows_by_visibility_and_title(pod: Pod):
    launch = await group(pod, title="Launch plan")
    secret = await group(pod, title="Secret plan", visibility="PRIVATE")
    await group(pod, title="100% done")
    owner = pod.client()

    def ids(listed) -> set[str]:
        assert listed.status_code == 200, listed.text
        return {item["id"] for item in listed.json()["items"]}

    private = await owner.get(
        pod.base, params={"kind": "GROUP", "visibility": "PRIVATE"}
    )
    assert ids(private) == {secret["id"]}
    assert ids(await owner.get(pod.base, params={"q": "LAUNCH"})) == {launch["id"]}
    assert ids(await owner.get(pod.base, params={"q": "plan"})) == {
        launch["id"],
        secret["id"],
    }
    # Typed as written: a ``%`` is a percent sign, not "anything".
    assert len(ids(await owner.get(pod.base, params={"q": "0% d"}))) == 1
    assert ids(await owner.get(pod.base, params={"q": "n%"})) == set()


async def test_a_channels_members_are_read_a_page_at_a_time(pod: Pod):
    crew = await group(
        pod, visibility="PRIVATE", members=[pod.editor["id"], pod.user["id"]]
    )
    path = f"{pod.base}/{crew['id']}/members"
    owner = pod.client()

    first = await owner.get(path, params={"limit": 2})
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 2
    cursor = first.json()["next_cursor"]
    assert cursor is not None
    rest = await owner.get(path, params={"limit": 2, "cursor": cursor})
    assert rest.status_code == 200, rest.text
    everyone = [
        item["person"]["id"] for item in first.json()["items"] + rest.json()["items"]
    ]
    assert sorted(everyone) == sorted(
        [pod.owner["id"], pod.editor["id"], pod.user["id"]]
    )
    assert rest.json()["next_cursor"] is None

    missing = await pod.client(pod.viewer).get(path)
    assert missing.status_code == 404, missing.text


async def test_a_snippet_keeps_the_words_as_written(pod: Pod):
    crew = await group(pod)
    await say(pod, crew["id"], "[live] the deploy is green")

    found = await pod.client().get(f"/pods/{pod.id}/search", params={"q": "live"})

    [hit] = found.json()["items"]
    assert hit["snippet"] == "[live] the deploy is green"


async def test_the_list_finds_a_channel_by_every_word_and_by_whose_it_is(
    pod: Pod, db_session
):
    await group(pod, title="Spring launch plan")
    await group(pod, title="Hiring")
    await contact_place(db_session, pod)
    await pod.client().post(f"{pod.base}/me")
    owner = pod.client()

    async def named(q: str, **params: str) -> list[str | None]:
        found = await owner.get(pod.base, params={"q": q, **params})
        assert found.status_code == 200, found.text
        return [
            item["title"] or item["person"]["name"] for item in found.json()["items"]
        ]

    assert await named("plan LAUNCH") == ["Spring launch plan"]
    assert await named("launch hiring") == []
    assert await named("dana") == ["Dana"]
    own = pod.owner["email"].split("@")[0]
    mine = await owner.get(pod.base, params={"q": own, "kind": "DIRECT"})
    assert [item["kind"] for item in mine.json()["items"]] == ["DIRECT"]
