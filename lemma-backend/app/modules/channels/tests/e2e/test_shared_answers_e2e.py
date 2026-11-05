"""An agent's answer made with one member's access, shared with the channel.

In a group, by default, it is shared as it is posted: who asked in front of
the group asked the group (``channels.answers_shared``). A group may keep
such answers to whoever asked instead: then everybody else in it is shown
that it was given, for whom, and not what it said; the member it was made for
is told so, and may share it (``…/messages/{id}/share``). Shared either way,
it reads in full for every member -- over REST, on the live socket, in
search, and in the background another member's run is handed -- and never for
a contact. A DIRECT or CONTACT channel is not a group: neither takes the
setting, and an answer there is posted as before.

Against Postgres through the API a member uses; the socket over a real
websocket.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.channels.contracts import (
    NewLine,
    Principal,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts.backgrounds import (
    BackgroundRefs,
    BackgroundView,
    read_backgrounds,
    refs_of,
)
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.domain.projection import Reader, may_read
from app.modules.channels.infrastructure.models import ChannelChangeModel
from app.modules.channels.tests.e2e.channel_pod import (
    Pod,
    contact_place,
    group,
    keep_answers_to_the_asker,
    say,
)
from app.modules.test_support.e2e.live_socket import live_socket
from app.modules.test_support.e2e.runtime import backend_server as _backend_server

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server

SAID = "The Q3 margin is 41 percent"


async def _answer_for_owner(pod: Pod, channel_id: str | UUID, text: str = SAID) -> str:
    """The pod's agent answers the owner in the channel, with their access."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await post(
            uow,
            channel_id=UUID(str(channel_id)),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id),
                    on_behalf_of=Principal.member(UUID(pod.owner["id"])),
                    display_name="Bolt",
                ),
                text=text,
            ),
        )
        await uow.commit()
    assert line is not None
    return str(line.id)


def _line(page, line_id: str) -> dict:
    [found] = [item for item in page.json()["items"] if item["id"] == line_id]
    return found


async def _changes(channel_id: str) -> int:
    async with async_session_maker() as session:
        return int(
            await session.scalar(
                select(func.count()).where(
                    ChannelChangeModel.channel_id == UUID(channel_id)
                )
            )
            or 0
        )


async def test_the_asker_shares_an_answer_and_everybody_reads_it(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])
    await keep_answers_to_the_asker(pod, crew["id"])
    answer = await _answer_for_owner(pod, crew["id"])
    lines = f"{pod.base}/{crew['id']}/messages"
    editor = pod.client(pod.editor)

    mine = _line(await pod.client().get(lines), answer)
    assert (mine["text"], mine["visibility"], mine["can_share"]) == (
        SAID,
        "asker_only",
        True,
    )
    theirs = _line(await editor.get(lines), answer)
    assert theirs["withheld"] and theirs["text"] is None
    assert (theirs["visibility"], theirs["can_share"]) == ("asker_only", False)
    search = await editor.get(f"/pods/{pod.id}/search", params={"q": "margin"})
    assert search.json()["items"] == []

    shared = await pod.client().post(f"{lines}/{answer}/share")
    assert shared.status_code == 200, shared.text
    body = shared.json()
    assert body["visibility"] == "channel" and body["shared_at"] is not None
    assert body["can_share"] is False

    theirs = _line(await editor.get(lines), answer)
    assert (theirs["withheld"], theirs["text"]) == (False, SAID)
    assert theirs["visibility"] == "channel"
    search = await editor.get(f"/pods/{pod.id}/search", params={"q": "margin"})
    assert [hit["message"]["id"] for hit in search.json()["items"]] == [answer]


async def test_only_the_member_it_was_made_for_may_share_it(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    await keep_answers_to_the_asker(pod, crew["id"])
    answer = await _answer_for_owner(pod, crew["id"])
    mine = await say(pod, crew["id"], "a member's own line")
    lines = f"{pod.base}/{crew['id']}/messages"

    refused = await pod.client(pod.editor).post(f"{lines}/{answer}/share")
    assert refused.status_code == 403, refused.text
    not_an_answer = await pod.client().post(f"{lines}/{mine['id']}/share")
    assert not_an_answer.status_code == 403, not_an_answer.text
    # Somebody who cannot see the channel cannot even find the line.
    private = await group(pod, title="Owners", visibility="PRIVATE")
    await keep_answers_to_the_asker(pod, private["id"])
    hidden = await _answer_for_owner(pod, private["id"])
    outside = await pod.client(pod.user).post(
        f"{pod.base}/{private['id']}/messages/{hidden}/share"
    )
    assert outside.status_code == 404, outside.text

    still = _line(await pod.client(pod.editor).get(lines), answer)
    assert still["withheld"]


async def test_sharing_twice_changes_nothing(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    await keep_answers_to_the_asker(pod, crew["id"])
    answer = await _answer_for_owner(pod, crew["id"])
    share = f"{pod.base}/{crew['id']}/messages/{answer}/share"

    first = await pod.client().post(share)
    changes = await _changes(crew["id"])
    again = await pod.client().post(share)

    assert again.status_code == 200, again.text
    assert again.json()["shared_at"] == first.json()["shared_at"]
    assert await _changes(crew["id"]) == changes, "a second share was a change"


async def test_a_shared_answer_reaches_another_members_run_but_no_contact(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    await keep_answers_to_the_asker(pod, crew["id"])
    answer = await _answer_for_owner(pod, crew["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await line_by_id(uow, UUID(answer))
    assert line is not None
    refs = BackgroundRefs(
        pod_id=UUID(pod.id),
        channel_id=UUID(crew["id"]),
        view=BackgroundView.MEMBER_IN_LEMMA,
        user_id=UUID(pod.editor["id"]),
        items=refs_of([line]),
    )

    async def editors_run_reads() -> tuple[list[str], int]:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            [shown] = await read_backgrounds(uow, [refs])
        return [said.text for said in shown.lines], shown.withheld

    assert await editors_run_reads() == ([], 1)
    assert (
        await pod.client().post(f"{pod.base}/{crew['id']}/messages/{answer}/share")
    ).status_code == 200
    assert await editors_run_reads() == ([SAID], 0)

    async with create_uow_from_session_maker(async_session_maker) as uow:
        shared = await line_by_id(uow, UUID(answer))
    assert shared is not None and shared.shared_at is not None
    assert not may_read(Reader.contact(UUID(pod.id), source_id=None), shared)


async def test_a_shared_answer_reaches_other_members_on_the_socket(
    pod: Pod, backend_server
) -> None:
    crew = await group(pod, members=[pod.editor["id"]])
    await keep_answers_to_the_asker(pod, crew["id"])
    answer = await _answer_for_owner(pod, crew["id"])
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as editor:
        await editor.subscribe("c", "channel", channel_id=crew["id"])
        shared = await pod.client().post(
            f"{pod.base}/{crew['id']}/messages/{answer}/share"
        )
        assert shared.status_code == 200, shared.text
        edited = await editor.event("c", "line.edited", message__id=answer)
    assert edited["message"]["text"] == SAID
    assert not edited["message"]["withheld"]
    assert edited["message"]["visibility"] == "channel"


# -- a group shows its agents' answers to everybody (the default) ---------------


async def test_in_a_group_everybody_reads_an_answer_asked_for_there(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])
    assert crew["answers_shared"] is True, "a group shows answers by default"
    answer = await _answer_for_owner(pod, crew["id"])
    lines = f"{pod.base}/{crew['id']}/messages"

    for reader in (pod.editor, pod.user):
        theirs = _line(await pod.client(reader).get(lines), answer)
        assert (theirs["withheld"], theirs["text"]) == (False, SAID), reader["email"]
        assert (theirs["visibility"], theirs["can_share"]) == ("channel", False)
        assert theirs["shared_at"] is not None
        assert theirs["on_behalf_of"]["id"] == pod.owner["id"]
    mine = _line(await pod.client().get(lines), answer)
    assert (mine["visibility"], mine["can_share"]) == ("channel", False)
    search = await pod.client(pod.editor).get(
        f"/pods/{pod.id}/search", params={"q": "margin"}
    )
    assert [hit["message"]["id"] for hit in search.json()["items"]] == [answer]
    # Shared with the pod's members, never with a contact.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await line_by_id(uow, UUID(answer))
    assert line is not None
    assert not may_read(Reader.contact(UUID(pod.id), source_id=None), line)


async def test_an_answer_asked_for_in_a_group_reaches_the_others_on_the_socket(
    pod: Pod, backend_server
) -> None:
    crew = await group(pod, members=[pod.editor["id"]])
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as editor:
        await editor.subscribe("c", "channel", channel_id=crew["id"])
        answer = await _answer_for_owner(pod, crew["id"])
        created = await editor.event("c", "line.created", message__id=answer)
    assert (created["message"]["text"], created["message"]["withheld"]) == (
        SAID,
        False,
    )
    assert created["message"]["visibility"] == "channel"


async def test_a_group_keeping_answers_to_the_asker_withholds_them_until_shared(
    pod: Pod,
):
    """Switched off, an answer is the asker's until they share it; one posted
    while it was on stays read by everybody."""
    crew = await group(pod, members=[pod.editor["id"]])
    before = await _answer_for_owner(pod, crew["id"], "posted while shown")
    changed = await keep_answers_to_the_asker(pod, crew["id"])
    assert changed["answers_shared"] is False
    after = await _answer_for_owner(pod, crew["id"])
    lines = f"{pod.base}/{crew['id']}/messages"
    editor = pod.client(pod.editor)

    page = await editor.get(lines)
    assert _line(page, before)["text"] == "posted while shown"
    withheld = _line(page, after)
    assert (withheld["withheld"], withheld["text"]) == (True, None)
    assert withheld["visibility"] == "asker_only" and withheld["shared_at"] is None
    assert _line(await pod.client().get(lines), after)["can_share"] is True

    assert (await pod.client().post(f"{lines}/{after}/share")).status_code == 200
    assert _line(await editor.get(lines), after)["text"] == SAID


async def test_a_direct_or_contacts_channel_posts_an_answer_as_before(
    pod: Pod, db_session
):
    """Neither is a group: an answer in the owner's own channel is theirs,
    and one in a contact's channel is still withheld from the other members
    who read it -- shared by nobody, not as it was posted."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        own = await direct_channel(
            uow, pod_id=UUID(pod.id), user_id=UUID(pod.owner["id"])
        )
        await uow.commit()
    in_own = await _answer_for_owner(pod, own.id)
    mine = _line(await pod.client().get(f"{pod.base}/{own.id}/messages"), in_own)
    assert (mine["text"], mine["shared_at"]) == (SAID, None)

    source, _ = await contact_place(db_session, pod)
    in_contacts = await _answer_for_owner(pod, source.channel_id)
    lines = f"{pod.base}/{source.channel_id}/messages"
    theirs = _line(await pod.client(pod.editor).get(lines), in_contacts)
    assert (theirs["withheld"], theirs["shared_at"]) == (True, None)
    detail = (await pod.client().get(f"{pod.base}/{source.channel_id}")).json()
    assert detail["answers_shared"] is None, "a contact's channel has no such setting"
    refused = await pod.client().patch(
        f"{pod.base}/{source.channel_id}", json={"answers_shared": True}
    )
    assert refused.status_code == 409, refused.text


async def test_only_who_may_manage_a_group_changes_whether_it_shows_answers(
    pod: Pod,
):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])
    path = f"{pod.base}/{crew['id']}"

    refused = await pod.client(pod.user).patch(path, json={"answers_shared": False})
    assert refused.status_code == 403, refused.text
    assert (await pod.client().get(path)).json()["answers_shared"] is True
    cleared = await pod.client().patch(path, json={"answers_shared": None})
    assert cleared.status_code == 422, cleared.text

    # An editor manages every group; who made one manages their own.
    by_editor = await pod.client(pod.editor).patch(path, json={"answers_shared": False})
    assert by_editor.status_code == 200, by_editor.text
    assert by_editor.json()["answers_shared"] is False
    theirs = await group(pod, title="users", as_user=pod.user)
    by_maker = await pod.client(pod.user).patch(
        f"{pod.base}/{theirs['id']}", json={"answers_shared": False}
    )
    assert by_maker.status_code == 200, by_maker.text
