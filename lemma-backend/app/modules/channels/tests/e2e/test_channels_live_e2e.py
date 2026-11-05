"""Channels on the pod's live socket -- over a real websocket, on Postgres.

A uvicorn server runs the app; each client is a ``websockets`` connection.
Lines are written through the REST API or the channels contracts, and reach
the socket by the channel's change counter and its Redis signal, never by a
test shortcut. The socket must never show what a GET would not.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from websockets.exceptions import ConnectionClosed

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live.config import live_settings
from app.core.server_limits import WS_MAX_BYTES
from app.modules.agent.infrastructure.live import publish_conversation_event
from app.modules.agent.domain.live_events import token_payload
from app.modules.channels.contracts import (
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    post,
)
from app.modules.channels.contracts import DeliveryReasonCode
from app.modules.channels.contracts.deliveries import delivery_skipped
from app.modules.channels.contracts.live import announce_answering
from app.modules.channels.tests.e2e.channel_pod import (
    Pod,
    an_asker,
    contact_place,
    group,
    keep_answers_to_the_asker,
    say,
)
from app.modules.test_support.e2e.live_socket import Frame, LiveClient, live_socket
from app.modules.test_support.e2e.runtime import backend_server as _backend_server

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server


def _ids(events: list[dict[str, object]]) -> list[object]:
    return [event["message"]["id"] for event in events]  # type: ignore[index]


# -- the timeline ------------------------------------------------------------------


async def test_a_channel_streams_its_lines_and_resumes_from_its_cursor(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Live")
    first = await say(pod, channel["id"], "before anyone watched")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        subscribed = await live.subscribe("c", "channel", channel_id=channel["id"])
        assert subscribed["type"] == "subscribed", subscribed
        second = await say(pod, channel["id"], "while watching")
        created = await live.event("c", "line.created", message__id=second["id"])
        # The REST line, as GET reads it.
        assert created["message"] == second
        cursor = live.cursor("c")
        assert cursor

    # Away: a line, an edit and a delete land.
    third = await say(pod, channel["id"], "while away")
    edited = await pod.client().patch(
        f"{pod.base}/{channel['id']}/messages/{second['id']}", json={"text": "fixed"}
    )
    assert edited.status_code == 200, edited.text
    gone = await pod.client().delete(
        f"{pod.base}/{channel['id']}/messages/{first['id']}"
    )
    assert gone.status_code == 204, gone.text

    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        resumed = await live.subscribe(
            "c", "channel", channel_id=channel["id"], since=cursor
        )
        assert resumed["type"] == "subscribed", resumed
        # It is told where it was, not the head: dropping before the gap
        # arrives resumes from here again rather than past what it missed.
        assert resumed["cursor"] == cursor
        assert (await live.event("c", "line.created", message__id=third["id"]))[
            "message"
        ]["text"] == "while away"
        assert (await live.event("c", "line.edited", message__id=second["id"]))[
            "message"
        ]["text"] == "fixed"
        deleted = await live.event("c", "line.deleted", message__id=first["id"])
        assert deleted["message"]["deleted"] and deleted["message"]["text"] is None
        # Nothing before the cursor is sent again.
        assert second["id"] not in _ids(live.events("c", "line.created"))

        fourth = await say(pod, channel["id"], "live again")
        await live.event("c", "line.created", message__id=fourth["id"])


async def test_a_line_removed_by_a_manager_reaches_its_watchers_as_theirs(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Moderated")
    said = await say(pod, channel["id"], "off topic", as_user=pod.user)
    async with live_socket(backend_server, pod.id, pod.user["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"])
        removed = await pod.client(pod.editor).delete(
            f"{pod.base}/{channel['id']}/messages/{said['id']}"
        )
        assert removed.status_code == 204, removed.text
        deleted = await live.event("c", "line.deleted", message__id=said["id"])
        assert deleted["message"]["text"] is None
        assert deleted["message"]["removed_by"]["id"] == pod.editor["id"]


async def test_a_cursor_not_ours_answers_resync(pod: Pod, backend_server) -> None:
    channel = await group(pod, title="Resync")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"], since="nope")
        resync = await live.until("resync", "c")
        assert resync["reason"] == "cursor_expired"


async def test_a_channel_change_reaches_its_watchers(pod: Pod, backend_server) -> None:
    channel = await group(pod, title="Before")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"])
        renamed = await pod.client().patch(
            f"{pod.base}/{channel['id']}", json={"title": "After", "topic": "plans"}
        )
        assert renamed.status_code == 200, renamed.text
        updated = await live.event("c", "channel.updated")
        assert updated["channel"]["title"] == "After"
        assert updated["channel"]["topic"] == "plans"


# -- who sees what -----------------------------------------------------------------


async def test_a_private_channel_never_reaches_who_is_not_in_it(
    pod: Pod, backend_server
) -> None:
    private = await group(
        pod, title="Secret", visibility="PRIVATE", members=[pod.editor["id"]]
    )
    async with (
        live_socket(backend_server, pod.id, pod.user["token"]) as outsider,
        live_socket(backend_server, pod.id, pod.editor["token"]) as insider,
    ):
        refused = await outsider.subscribe("c", "channel", channel_id=private["id"])
        assert refused["type"] == "error" and refused["code"] == "not_found"
        assert (await outsider.subscribe("i", "inbox"))["type"] == "subscribed"
        assert (await insider.subscribe("c", "channel", channel_id=private["id"]))[
            "type"
        ] == "subscribed"

        line = await say(pod, private["id"], "the secret")
        await insider.event("c", "line.created", message__id=line["id"])
        await outsider.quiet()
        assert "the secret" not in str(outsider.frames)
        assert private["id"] not in str(outsider.frames)

        # Taken out of it, the insider stops hearing it at once.
        removed = await pod.client().delete(
            f"{pod.base}/{private['id']}/members/MEMBER:{pod.editor['id']}"
        )
        assert removed.status_code == 204, removed.text
        ended = await insider.until("unsubscribed", "c")
        assert ended["reason"] == "access_revoked"
        await say(pod, private["id"], "after they left")
        await insider.quiet()
        assert "after they left" not in str(insider.frames)


async def test_a_contacts_channel_needs_contact_read(
    pod: Pod, db_session: AsyncSession, backend_server
) -> None:
    source, contact_id = await contact_place(db_session, pod)
    channel_id = str(source.channel_id)
    async with (
        live_socket(backend_server, pod.id, pod.user["token"]) as user,
        live_socket(backend_server, pod.id, pod.editor["token"]) as editor,
    ):
        refused = await user.subscribe("c", "channel", channel_id=channel_id)
        assert refused["type"] == "error" and refused["code"] == "not_found"
        assert (await editor.subscribe("c", "channel", channel_id=channel_id))[
            "type"
        ] == "subscribed"
        line = await post(
            SqlAlchemyUnitOfWork(db_session),
            channel_id=source.channel_id,
            line=NewLine(
                sender=Sender.contact(contact_id), text="Any news?", source_id=source.id
            ),
        )
        await db_session.commit()
        assert line is not None
        seen = await editor.event("c", "line.created", message__id=str(line.id))
        assert seen["message"]["text"] == "Any news?"


async def test_an_answer_withheld_over_rest_is_withheld_on_the_socket(
    pod: Pod, db_session: AsyncSession, backend_server
) -> None:
    channel = await group(pod, title="Answers", members=[pod.editor["id"]])
    await keep_answers_to_the_asker(pod, channel["id"])
    agent = await pod.scenario.create_agent()
    async with (
        live_socket(backend_server, pod.id, pod.owner["token"]) as owner,
        live_socket(backend_server, pod.id, pod.editor["token"]) as editor,
    ):
        for live in (owner, editor):
            await live.subscribe("c", "channel", channel_id=channel["id"])
        line = await post(
            SqlAlchemyUnitOfWork(db_session),
            channel_id=UUID(channel["id"]),
            line=NewLine(
                sender=Sender.agent(
                    UUID(agent["id"]),
                    on_behalf_of=Principal.member(UUID(pod.owner["id"])),
                    display_name="Helper",
                ),
                text="made with the owner's own access",
            ),
        )
        await db_session.commit()
        assert line is not None
        mine = await owner.event("c", "line.created", message__id=str(line.id))
        theirs = await editor.event("c", "line.created", message__id=str(line.id))
    assert mine["message"]["text"] == "made with the owner's own access"
    assert not mine["message"]["withheld"]
    assert theirs["message"]["withheld"] and theirs["message"]["text"] is None
    over_rest = await pod.client(pod.editor).get(f"{pod.base}/{channel['id']}/messages")
    assert theirs["message"] in over_rest.json()["items"]


# -- the inbox, reading, questions ---------------------------------------------------


async def test_inbox_counts_move_on_a_line_and_on_a_read(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Counts", members=[pod.editor["id"]])
    async with (
        live_socket(backend_server, pod.id, pod.editor["token"]) as editor,
        live_socket(backend_server, pod.id, pod.editor["token"]) as other_tab,
    ):
        await editor.subscribe("i", "inbox")
        await other_tab.subscribe("c", "channel", channel_id=channel["id"])
        line = await say(pod, channel["id"], "news")
        moved = await editor.event(
            "i", "inbox.updated", channel_id=channel["id"], unread__lines=1
        )
        assert moved["totals"]["channels"] >= 1

        read = await editor.call("read", channel_id=channel["id"], seq=line["seq"])
        assert read["type"] == "result", read
        assert read["result"]["read_seq"] == line["seq"]
        await editor.event(
            "i", "inbox.updated", channel_id=channel["id"], unread__lines=0
        )
        # This read's own event. The tab subscribed just after the editor was
        # put in the group, and that can reach it as a `read.updated` at 0.
        heard = await other_tab.event(
            "c", "read.updated", channel_id=channel["id"], read_seq=line["seq"]
        )
        assert heard["read_seq"] == line["seq"]

    # The call is the REST read: the same position, the same rule.
    detail = await pod.client(pod.editor).get(f"{pod.base}/{channel['id']}")
    assert detail.json()["me"]["read_seq"] == line["seq"]
    again = await pod.client(pod.editor).post(
        f"{pod.base}/{channel['id']}/read", json={"seq": 0}
    )
    assert again.json()["read_seq"] == line["seq"]


async def test_a_channel_out_of_sight_clears_from_the_inbox(
    pod: Pod, backend_server
) -> None:
    """Taken out of a private group, a reader's badge for it goes to nothing
    -- once -- and the group is never named to them again."""
    private = await group(
        pod, title="Inner", visibility="PRIVATE", members=[pod.editor["id"]]
    )
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as editor:
        await editor.subscribe("i", "inbox")
        await say(pod, private["id"], "for the few")
        await editor.event(
            "i", "inbox.updated", channel_id=private["id"], unread__lines=1
        )

        removed = await pod.client().delete(
            f"{pod.base}/{private['id']}/members/MEMBER:{pod.editor['id']}"
        )
        assert removed.status_code == 204, removed.text
        cleared = await editor.event(
            "i", "inbox.updated", channel_id=private["id"], unread__lines=0
        )
        assert cleared["unread"] == {
            "lines": 0,
            "mentions": 0,
            "waiting": 0,
            "read_seq": 0,
        }
        told = len(editor.events("i", "inbox.updated"))
        await say(pod, private["id"], "after they left")
        await editor.quiet()
        assert len(editor.events("i", "inbox.updated")) == told


async def test_a_thread_reply_sends_its_root_again_with_the_count(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Threads")
    root = await say(pod, channel["id"], "release notes?")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"])
        reply = await say(pod, channel["id"], "drafting", thread_root_id=root["id"])
        await live.event("c", "line.created", message__id=reply["id"])
        again = await live.event("c", "line.edited", message__id=root["id"])
        assert again["message"]["reply_count"] == 1


async def test_a_send_that_moves_reaches_its_writer_on_the_line(
    pod: Pod, db_session: AsyncSession, backend_server
) -> None:
    source, _ = await contact_place(db_session, pod)
    channel_id = str(source.channel_id)
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as editor:
        await editor.subscribe("c", "channel", channel_id=channel_id)
        said = await pod.client(pod.editor).post(
            f"{pod.base}/{channel_id}/messages", json={"text": "Shipped today"}
        )
        assert said.status_code == 201, said.text
        [went] = said.json()["delivered"]
        await delivery_skipped(
            SqlAlchemyUnitOfWork(db_session),
            UUID(went["id"]),
            code=DeliveryReasonCode.OUTSIDE_WINDOW,
            reason="Outside the window",
        )
        await db_session.commit()
        # Read by the hub with its post, or after it: the line as it is now.
        for _ in range(40):
            seen = [
                event
                for event in editor.events("c")
                if event["type"] in {"line.created", "line.edited"}
                and event["message"]["id"] == said.json()["id"]
                and event["message"]["delivered"][0]["status"] == "SKIPPED"
            ]
            if seen:
                break
            await editor.quiet(0.25)
        assert seen, editor.events("c")
        assert seen[-1]["message"]["delivered"][0]["reason"] == "Outside the window"


async def test_a_read_over_rest_moves_the_readers_own_inbox_too(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Read here", members=[pod.editor["id"]])
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as editor:
        await editor.subscribe("i", "inbox")
        line = await say(pod, channel["id"], "catch up")
        await editor.event(
            "i", "inbox.updated", channel_id=channel["id"], unread__lines=1
        )
        read = await pod.client(pod.editor).post(
            f"{pod.base}/{channel['id']}/read", json={"seq": line["seq"]}
        )
        assert read.status_code == 200, read.text
        cleared = await editor.event(
            "i", "inbox.updated", channel_id=channel["id"], unread__lines=0
        )
        assert cleared["unread"]["read_seq"] == line["seq"]


async def test_a_read_call_on_a_channel_they_cannot_see_is_not_found(
    pod: Pod, backend_server
) -> None:
    private = await group(pod, title="Hidden", visibility="PRIVATE")
    async with live_socket(backend_server, pod.id, pod.user["token"]) as user:
        refused = await user.call("read", channel_id=private["id"], seq=1)
        assert refused["type"] == "error" and refused["code"] == "not_found"


async def test_a_question_waits_in_the_inbox_and_closes_on_the_timeline(
    pod: Pod, db_session: AsyncSession, backend_server
) -> None:
    channel = await group(pod, title="Asks")
    owner = UUID(pod.owner["id"])
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("i", "inbox")
        await live.subscribe("c", "channel", channel_id=channel["id"])
        line = await post(
            SqlAlchemyUnitOfWork(db_session),
            channel_id=UUID(channel["id"]),
            line=NewLine(sender=Sender.member(owner), text="may I?"),
            question=NewQuestion(
                kind=QuestionKind.APPROVAL,
                ref=f"e2e:{uuid4()}",
                asker=await an_asker(UUID(pod.id), owner),
                question={"prompt": "May I?"},
                awaits=Principal.member(owner),
            ),
        )
        await db_session.commit()
        assert line is not None
        waiting = await live.event("i", "question.waiting", message_id=str(line.id))
        assert waiting["message"]["question"]["state"] == "OPEN"

        answered = await pod.client().post(
            f"{pod.base}/{channel['id']}/messages/{line.id}/answer",
            json={"answer": {"decision": "DENY"}},
        )
        assert answered.status_code == 200, answered.text
        closed = await live.event("c", "question.closed", message_id=str(line.id))
        assert closed["state"] == "ANSWERED"
        edited = [
            event
            for event in live.events("c", "line.edited")
            if event["message"]["id"] == str(line.id)
        ]
        assert edited[-1]["message"]["question"]["answer"] == {"decision": "DENY"}


# -- ephemeral: typing, answering ----------------------------------------------------


async def test_typing_reaches_the_others_and_not_oneself(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Typing", members=[pod.editor["id"]])
    async with (
        live_socket(backend_server, pod.id, pod.owner["token"]) as owner,
        live_socket(backend_server, pod.id, pod.editor["token"]) as editor,
    ):
        for live in (owner, editor):
            await live.subscribe("c", "channel", channel_id=channel["id"])
        typed = await owner.call("typing", channel_id=channel["id"])
        assert typed["type"] == "result", typed
        heard = await editor.event("c", "typing", channel_id=channel["id"])
        assert heard["user"]["id"] == pod.owner["id"]
        await owner.quiet()
        assert owner.events("c", "typing") == []


async def test_typing_needs_channel_write_and_is_rate_limited(
    pod: Pod, backend_server, monkeypatch
) -> None:
    channel = await group(pod, title="Quiet please")
    monkeypatch.setattr(live_settings, "live_typing_calls_per_minute", 2)
    async with (
        live_socket(backend_server, pod.id, pod.viewer["token"]) as viewer,
        live_socket(backend_server, pod.id, pod.owner["token"]) as owner,
    ):
        refused = await viewer.call("typing", channel_id=channel["id"])
        assert refused["type"] == "error" and refused["code"] == "forbidden"
        answers = [
            (await owner.call("typing", channel_id=channel["id"]))["type"]
            for _ in range(3)
        ]
        assert answers == ["result", "result", "error"]


async def test_an_answer_being_written_reaches_only_who_it_is_for(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Streaming", members=[pod.editor["id"]])
    line = await say(pod, channel["id"], "@helper what is new?")
    conversation_id, run_id, agent_id = uuid4(), uuid4(), uuid4()
    async with (
        live_socket(backend_server, pod.id, pod.owner["token"]) as owner,
        live_socket(backend_server, pod.id, pod.editor["token"]) as editor,
    ):
        for live in (owner, editor):
            await live.subscribe("c", "channel", channel_id=channel["id"])
        announce_answering(
            pod_id=UUID(pod.id),
            channel_id=UUID(channel["id"]),
            message_id=UUID(line["id"]),
            agent_id=agent_id,
            for_user_id=UUID(pod.owner["id"]),
            state="started",
            conversation_id=conversation_id,
            agent_run_id=run_id,
            stream=("agent.conversation", str(conversation_id)),
        )
        mine = await owner.event("c", "answering.started", message_id=line["id"])
        theirs = await editor.event("c", "answering.started", message_id=line["id"])
        assert mine["conversation_id"] == str(conversation_id)
        assert "conversation_id" not in theirs

        for _ in range(20):
            await publish_conversation_event(
                conversation_id, token_payload(run_id, "Hello")
            )
            if owner.events("c", "line.streaming"):
                break
            await owner.quiet(0.25)
        delta = await owner.event("c", "line.streaming", message_id=line["id"])
        assert delta["delta"] == "Hello" and delta["agent_run_id"] == str(run_id)

        announce_answering(
            pod_id=UUID(pod.id),
            channel_id=UUID(channel["id"]),
            message_id=UUID(line["id"]),
            agent_id=agent_id,
            for_user_id=UUID(pod.owner["id"]),
            state="finished",
        )
        await editor.event("c", "answering.finished", message_id=line["id"])
        await owner.event("c", "answering.finished", message_id=line["id"])
        assert editor.events("c", "line.streaming") == []


async def test_a_reader_joining_mid_answer_is_sent_it_so_far(
    pod: Pod, backend_server
) -> None:
    """The member an answer is for opens the channel while it is being
    written: "answering", the text so far in one piece, then what follows.
    Anybody else joining is told only that it is being answered."""
    channel = await group(pod, title="Late", members=[pod.editor["id"]])
    line = await say(pod, channel["id"], "@helper summarise the week")
    conversation_id, run_id, agent_id = uuid4(), uuid4(), uuid4()

    def answering(state: str, **more: object) -> None:
        announce_answering(
            pod_id=UUID(pod.id),
            channel_id=UUID(channel["id"]),
            message_id=UUID(line["id"]),
            agent_id=agent_id,
            for_user_id=UUID(pod.owner["id"]),
            state=state,  # type: ignore[arg-type]
            **more,  # type: ignore[arg-type]
        )

    async with live_socket(backend_server, pod.id, pod.owner["token"]) as early:
        await early.subscribe("c", "channel", channel_id=channel["id"])
        answering(
            "started",
            conversation_id=conversation_id,
            agent_run_id=run_id,
            stream=("agent.conversation", str(conversation_id)),
        )
        await early.event("c", "answering.started", message_id=line["id"])
        # Written until the relay is surely listening, then some more.
        for _ in range(40):
            await publish_conversation_event(
                conversation_id, token_payload(run_id, "Mon. ")
            )
            if early.events("c", "line.streaming"):
                break
            await early.quiet(0.25)
        await publish_conversation_event(conversation_id, token_payload(run_id, "Tue."))
        await early.event("c", "line.streaming", delta="Tue.")
        so_far = "".join(
            str(event["delta"]) for event in early.events("c", "line.streaming")
        )

        async with (
            live_socket(backend_server, pod.id, pod.owner["token"]) as late,
            live_socket(backend_server, pod.id, pod.editor["token"]) as other,
        ):
            await late.subscribe("c", "channel", channel_id=channel["id"])
            await other.subscribe("c", "channel", channel_id=channel["id"])
            started = await late.event("c", "answering.started", message_id=line["id"])
            assert started["conversation_id"] == str(conversation_id)
            snapshot = await late.event("c", "line.streaming", delta=so_far)
            assert snapshot["kind"] == "snapshot" and so_far.endswith("Mon. Tue.")
            await publish_conversation_event(
                conversation_id, token_payload(run_id, " Wed.")
            )
            await late.event("c", "line.streaming", delta=" Wed.")

            told = await other.event("c", "answering.started", message_id=line["id"])
            assert "conversation_id" not in told
            answering("finished")
            await other.event("c", "answering.finished", message_id=line["id"])
            assert other.events("c", "line.streaming") == []


async def test_typing_is_limited_per_person_across_their_sockets(
    pod: Pod, backend_server, monkeypatch
) -> None:
    channel = await group(pod, title="Chatty")
    monkeypatch.setattr(live_settings, "live_typing_calls_per_user_per_minute", 2)
    async with (
        live_socket(backend_server, pod.id, pod.owner["token"]) as one,
        live_socket(backend_server, pod.id, pod.owner["token"]) as two,
    ):
        answers = [
            (await socket.call("typing", channel_id=channel["id"]))
            for socket in (one, two, two)
        ]
    assert [answer["type"] for answer in answers] == ["result", "result", "error"]
    assert answers[-1]["code"] == "limit"


# -- limits ---------------------------------------------------------------------------


async def test_subscriptions_frames_and_unknown_topics_are_refused_in_place(
    pod: Pod, backend_server, monkeypatch
) -> None:
    channel = await group(pod, title="Limits")
    monkeypatch.setattr(live_settings, "live_max_subscriptions_per_socket", 2)
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        assert set(live.ready["topics"]) >= {"channel", "inbox", "records"}
        assert set(live.ready["calls"]) >= {"typing", "read", "presence"}
        assert (await live.subscribe("a", "channel", channel_id=channel["id"]))[
            "type"
        ] == "subscribed"
        assert (await live.subscribe("b", "inbox"))["type"] == "subscribed"
        limit = await live.subscribe("c", "inbox")
        assert limit["type"] == "error" and limit["code"] == "limit"
        unknown = await live.call("dance")
        assert unknown["code"] == "invalid"
        await live.ws.send("x" * (17 * 1024))
        too_big = await live.until("error")
        assert too_big["code"] == "invalid"
        await live.send(op="unsubscribe", id="b")
        await live.until("unsubscribed", "b")
        missing = await live.subscribe("d", "channel", channel_id=str(uuid4()))
        assert missing["code"] == "not_found"
        presence = await live.call("presence")
        assert pod.owner["id"] in presence["result"]["online"]


# -- catching up: more than one read, and cursors honest ------------------------------

#: More lines than a read of the timeline returns (``rows.MAX_LINES``).
_BURST = 320


async def _burst(db_session: AsyncSession, pod: Pod, channel_id: str) -> list[str]:
    """``_BURST`` lines in one transaction: one change of the channel for the
    hub to hear, however many lines it holds."""
    uow = SqlAlchemyUnitOfWork(db_session)
    ids: list[str] = []
    for n in range(_BURST):
        line = await post(
            uow,
            channel_id=UUID(channel_id),
            line=NewLine(sender=Sender.member(UUID(pod.owner["id"])), text=f"b{n}"),
        )
        assert line is not None
        ids.append(str(line.id))
    await db_session.commit()
    return ids


async def _created_or_resync(
    live: LiveClient, sub: str, wanted: list[str], *, timeout: float = 30
) -> tuple[set[str], Frame | None]:
    """The lines of ``wanted`` sent as created on ``sub`` until all are, or
    the resync that stood in for them."""

    async def _read() -> tuple[set[str], Frame | None]:
        while True:
            seen = set(_ids(live.events(sub, "line.created")))
            if set(wanted) <= seen:
                return seen, None
            frame = await live.recv(timeout)
            if frame["type"] == "resync" and frame.get("id") == sub:
                return seen, frame

    return await asyncio.wait_for(_read(), timeout)


def _seq(cursor: object) -> int:
    return int(str(cursor).partition(":")[2])


async def test_resuming_past_one_read_sends_every_line_or_resyncs(
    pod: Pod, db_session: AsyncSession, backend_server, monkeypatch
) -> None:
    """Away for more lines than one read returns: all of them come back, or,
    past the batch limit, a resync -- never some, labelled caught up."""
    channel = await group(pod, title="Away long")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"])
        cursor = live.cursor("c")
    ids = await _burst(db_session, pod, channel["id"])

    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"], since=cursor)
        seen, resync = await _created_or_resync(live, "c", ids)
        assert resync is None, resync
        assert set(ids) <= seen

    # Fewer than were said (``_BURST``).
    monkeypatch.setattr(live_settings, "live_max_changes_per_batch", 250)
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"], since=cursor)
        seen, resync = await _created_or_resync(live, "c", ids)
        assert resync is not None and resync["reason"] == "too_many", seen
        assert not seen, "a resync was sent beside some of the lines"


async def test_a_burst_past_one_read_reaches_a_watcher_whole(
    pod: Pod, db_session: AsyncSession, backend_server
) -> None:
    channel = await group(pod, title="Burst")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"])
        ids = await _burst(db_session, pod, channel["id"])
        seen, resync = await _created_or_resync(live, "c", ids)
        assert resync is None, resync
        assert set(ids) <= seen


async def test_lines_arrive_once_and_never_past_their_batch_cursor(
    pod: Pod, backend_server
) -> None:
    """A line committed while the hub reads is the next batch's: not sent
    ahead of the cursor it rides under, and then again."""
    channel = await group(pod, title="Steady")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("c", "channel", channel_id=channel["id"])
        said: list[str] = []
        for _ in range(12):
            lines = await asyncio.gather(
                *(say(pod, channel["id"], "steady") for _ in range(5))
            )
            said.extend(line["id"] for line in lines)
        await _created_or_resync(live, "c", said)
        await live.quiet()
    created = _ids(live.events("c", "line.created"))
    assert len(created) == len(set(created)), "a line was created twice"
    for frame in live.frames:
        if frame["type"] != "events" or frame.get("id") != "c":
            continue
        for event in frame["events"]:  # type: ignore[union-attr]
            if event["type"].startswith("line."):
                assert event["message"]["seq"] <= _seq(frame["cursor"]), frame


async def test_a_bad_cursor_is_not_echoed_and_an_impossible_one_resyncs(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Cursors")
    await say(pod, channel["id"], "one")
    await say(pod, channel["id"], "two")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        head = (await live.subscribe("h", "channel", channel_id=channel["id"]))[
            "cursor"
        ]
        bad = await live.subscribe(
            "b", "channel", channel_id=channel["id"], since="x:y"
        )
        assert bad["cursor"] == head
        assert (await live.until("resync", "b"))["reason"] == "cursor_expired"

        # Its change behind the head, its line ahead of it: no cursor given out.
        told = await live.subscribe(
            "i", "channel", channel_id=channel["id"], since=f"1:{_seq(head) + 50}"
        )
        assert told["cursor"] == head
        assert (await live.until("resync", "i"))["reason"] == "cursor_expired"
        await live.quiet()
        assert live.events("i", "line.edited") == []


async def test_a_read_past_what_a_line_number_holds_is_invalid(
    pod: Pod, backend_server
) -> None:
    channel = await group(pod, title="Huge")
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        for seq in (2**63, 10**30):
            refused = await live.call("read", channel_id=channel["id"], seq=seq)
            assert refused["type"] == "error", refused
            assert refused["code"] == "invalid", refused


async def test_the_frame_limit_counts_bytes(pod: Pod, backend_server) -> None:
    """Fewer characters than the limit, more bytes: refused."""
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.send(op="ping", pad="€" * 6000)
        refused = await live.until("error")
        assert refused["code"] == "invalid" and "too large" in str(refused["message"])
        await live.send(op="ping")
        await live.until("pong")


async def test_a_message_past_the_servers_cap_closes_the_socket_with_1009(
    pod: Pod, backend_server
) -> None:
    """The server refuses it while it arrives -- never read whole into memory
    to be refused after. Under the cap, a frame too large for the protocol is
    refused in place and the socket carries on."""
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.send(op="ping", pad="x" * (WS_MAX_BYTES - 1024))
        refused = await live.until("error")
        assert refused["code"] == "invalid" and "too large" in str(refused["message"])
        await live.ws.send("x" * (WS_MAX_BYTES + 1))
        with pytest.raises(ConnectionClosed):
            await live.recv(timeout=10)
        assert live.ws.close_code == 1009
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as again:
        await again.send(op="ping")
        await again.until("pong")


async def test_a_change_that_moves_none_of_a_readers_counts_sends_them_nothing(
    pod: Pod, backend_server, monkeypatch
) -> None:
    # Polls inside the quiet windows: a poll reads back the seat the editor
    # was given just before subscribing, and must not send it as news.
    monkeypatch.setattr(live_settings, "live_idle_poll_seconds", 0.25)
    elsewhere = await group(pod, title="Not theirs")
    theirs = await group(pod, title="Theirs", members=[pod.editor["id"]])
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as editor:
        await editor.subscribe("i", "inbox")
        await say(pod, elsewhere["id"], "nothing to them")
        await editor.quiet()
        await say(pod, theirs["id"], "news for them")
        await editor.event("i", "inbox.updated", channel_id=theirs["id"])
        await editor.quiet()
        updates = editor.events("i", "inbox.updated")
    assert [update["channel_id"] for update in updates] == [theirs["id"]], updates


async def test_a_question_after_many_lines_in_one_change_still_waits(
    pod: Pod, db_session: AsyncSession, backend_server
) -> None:
    """The inbox looks for new questions by asking for them, not in the
    first lines of a change."""
    channel = await group(pod, title="Busy asks")
    owner = UUID(pod.owner["id"])
    async with live_socket(backend_server, pod.id, pod.owner["token"]) as live:
        await live.subscribe("i", "inbox")
        uow = SqlAlchemyUnitOfWork(db_session)
        for n in range(60):
            await post(
                uow,
                channel_id=UUID(channel["id"]),
                line=NewLine(sender=Sender.member(owner), text=f"chatter {n}"),
            )
        line = await post(
            uow,
            channel_id=UUID(channel["id"]),
            line=NewLine(sender=Sender.member(owner), text="and then: may I?"),
            question=NewQuestion(
                kind=QuestionKind.APPROVAL,
                ref=f"e2e:{uuid4()}",
                asker=await an_asker(UUID(pod.id), owner),
                question={"prompt": "May I?"},
                awaits=Principal.member(owner),
            ),
        )
        await db_session.commit()
        assert line is not None
        await live.event("i", "question.waiting", message_id=str(line.id))
