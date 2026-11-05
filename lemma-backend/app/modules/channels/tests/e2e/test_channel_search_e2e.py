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


async def test_a_snippet_keeps_the_words_as_written(pod: Pod):
    crew = await group(pod)
    await say(pod, crew["id"], "[live] the deploy is green")

    found = await pod.client().get(f"/pods/{pod.id}/search", params={"q": "live"})

    [hit] = found.json()["items"]
    assert hit["snippet"] == "[live] the deploy is green"
