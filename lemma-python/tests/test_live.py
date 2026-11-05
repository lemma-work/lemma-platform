"""The live-socket client: the protocol, and a real socket against a scripted server."""

from __future__ import annotations

import json

import pytest
from websockets.asyncio.server import ServerConnection, serve

from lemma_sdk.live import (
    LiveAccessError,
    LiveChange,
    LiveEnded,
    LiveReady,
    LiveResync,
    LiveSession,
    LiveTopicEvent,
    live_ws_url,
    watch_changes,
)


def test_live_url_follows_the_api_scheme() -> None:
    assert live_ws_url("https://api.x.test/", "P") == "wss://api.x.test/pods/P/live"
    assert (
        live_ws_url("http://localhost:8711", "P") == "ws://localhost:8711/pods/P/live"
    )
    assert live_ws_url("api.x.test", "P") == "wss://api.x.test/pods/P/live"


def test_session_sends_when_with_its_subscription() -> None:
    session = LiveSession()
    open_ = session.add(table="tickets", when={"status": {"in": ["open"]}})
    assert [json.loads(f) for f in session.subscribe_frames()] == [
        {
            "op": "subscribe",
            "id": open_,
            "topic": "records",
            "table": "tickets",
            "when": {"status": {"in": ["open"]}},
        }
    ]


def test_session_tracks_each_subscriptions_cursor_across_reconnects() -> None:
    session = LiveSession()
    tickets = session.add(table="tickets", since="c.0")
    everything = session.add(ops=["delete"])
    assert [json.loads(f) for f in session.subscribe_frames()] == [
        {
            "op": "subscribe",
            "id": tickets,
            "topic": "records",
            "table": "tickets",
            "since": "c.0",
        },
        {"op": "subscribe", "id": everything, "topic": "records", "ops": ["delete"]},
    ]

    assert session.handle('{"type":"ready","connection":"x"}') == (True, [])
    _, events = session.handle(
        json.dumps({"type": "subscribed", "id": tickets, "cursor": "c.1"})
    )
    assert events == [LiveReady(tickets, "c.1")]
    _, events = session.handle(
        json.dumps(
            {
                "type": "changes",
                "id": tickets,
                "cursor": "c.4",
                "changes": [
                    {
                        "op": "update",
                        "table": "tickets",
                        "record_id": "r",
                        "row": {"v": 2},
                    },
                    {"op": "delete", "table": "tickets", "record_id": "q", "row": None},
                ],
            }
        )
    )
    assert events == [
        LiveChange(tickets, "update", "tickets", "r", {"v": 2}, "c.4"),
        LiveChange(tickets, "delete", "tickets", "q", None, "c.4"),
    ]
    _, events = session.handle(
        json.dumps(
            {
                "type": "resync",
                "id": everything,
                "stream_id": "c.9",
                "reason": "too_many",
            }
        )
    )
    assert events == [LiveResync(everything, "c.9", "too_many")]

    resumed = [json.loads(f).get("since") for f in session.subscribe_frames()]
    assert resumed == ["c.4", "c.9"]


def test_session_carries_a_channel_topics_events_and_resumes_after_them() -> None:
    session = LiveSession()
    channel = session.add(topic="channel", params={"channel_id": "C"})
    inbox = session.add(topic="inbox")
    assert [json.loads(f) for f in session.subscribe_frames()] == [
        {"channel_id": "C", "op": "subscribe", "id": channel, "topic": "channel"},
        {"op": "subscribe", "id": inbox, "topic": "inbox"},
    ]
    line = {"type": "line.created", "message": {"id": "m", "text": "hi"}}
    _, events = session.handle(
        json.dumps({"type": "events", "id": channel, "cursor": "7:3", "events": [line]})
    )
    assert events == [LiveTopicEvent(channel, "line.created", line, "7:3")]
    counts = {"type": "inbox.updated", "channel_id": "C"}
    _, events = session.handle(
        json.dumps({"type": "events", "id": inbox, "cursor": None, "events": [counts]})
    )
    assert events == [LiveTopicEvent(inbox, "inbox.updated", counts, "")]
    # Reconnected, the channel resumes after what it was sent.
    assert json.loads(session.subscribe_frames()[0])["since"] == "7:3"


def test_session_drops_a_refused_or_revoked_subscription() -> None:
    session = LiveSession()
    refused = session.add(table="nope")
    revoked = session.add(table="secret")
    _, events = session.handle(
        json.dumps({"type": "error", "id": refused, "code": "not_found"})
    )
    assert events == [LiveEnded(refused, "refused: not_found")]
    _, events = session.handle(
        json.dumps({"type": "unsubscribed", "id": revoked, "reason": "access_revoked"})
    )
    assert events == [LiveEnded(revoked, "access_revoked")]
    assert not session.active
    assert session.handle("not json") == (False, [])


@pytest.mark.asyncio
async def test_watch_changes_resumes_from_its_cursor_after_a_drop() -> None:
    connections: list[list[dict]] = []

    async def handler(ws: ServerConnection) -> None:
        received: list[dict] = []
        connections.append(received)
        assert ws.request is not None
        assert ws.request.headers["Authorization"] == "Bearer T"
        await ws.send(json.dumps({"type": "ready", "connection": "c"}))
        received.append(json.loads(await ws.recv()))
        if len(connections) == 1:
            await ws.send(
                json.dumps({"type": "subscribed", "id": "s1", "cursor": "c.1"})
            )
            await ws.send(
                json.dumps(
                    {
                        "type": "changes",
                        "id": "s1",
                        "cursor": "c.2",
                        "changes": [
                            {"op": "insert", "table": "t", "record_id": "a", "row": {}}
                        ],
                    }
                )
            )
            await ws.close(1011)
            return
        await ws.send(json.dumps({"type": "subscribed", "id": "s1", "cursor": "c.2"}))
        await ws.send(
            json.dumps(
                {"type": "resync", "id": "s1", "cursor": "c.7", "reason": "too_many"}
            )
        )
        await ws.send(
            json.dumps({"type": "unsubscribed", "id": "s1", "reason": "access_revoked"})
        )
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        events = [
            event
            async for event in watch_changes(
                base_url=f"http://127.0.0.1:{port}",
                pod_id="P",
                token=lambda: "T",
                table="t",
                max_reconnects=3,
            )
        ]

    assert [type(event).__name__ for event in events] == [
        "LiveReady",
        "LiveChange",
        "LiveReady",
        "LiveResync",
        "LiveEnded",
    ]
    assert connections[0][0] == {
        "op": "subscribe",
        "id": "s1",
        "topic": "records",
        "table": "t",
    }
    assert connections[1][0]["since"] == "c.2"


@pytest.mark.asyncio
async def test_watch_changes_refreshes_once_then_stops_on_forbidden() -> None:
    closes = [4401, 4403]
    refreshes: list[bool] = []

    async def handler(ws: ServerConnection) -> None:
        await ws.close(closes.pop(0))

    def refresh() -> bool:
        refreshes.append(True)
        return True

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(LiveAccessError):
            async for _ in watch_changes(
                base_url=f"http://127.0.0.1:{port}",
                pod_id="P",
                token=lambda: "T",
                refresh=refresh,
            ):
                pass
    assert refreshes == [True]


def test_session_keeps_an_unavailable_subscription_and_retries_it() -> None:
    session = LiveSession(max_retries=2)
    sub = session.add(table="t", since="c.3")
    for _ in range(2):
        _, events = session.handle(
            json.dumps({"type": "error", "id": sub, "code": "unavailable"})
        )
        assert events == [] and session.active
        ((frame, delay),) = session.take_retries()
        assert json.loads(frame)["since"] == "c.3" and delay >= 0
    _, events = session.handle(
        json.dumps({"type": "error", "id": sub, "code": "unavailable"})
    )
    assert events == [LiveEnded(sub, "max retry attempts reached (unavailable)")]
    assert not session.active


def test_a_subscribed_resets_the_retry_count() -> None:
    session = LiveSession(max_retries=1)
    sub = session.add(table="t")
    session.handle(json.dumps({"type": "error", "id": sub, "code": "unavailable"}))
    session.handle(json.dumps({"type": "subscribed", "id": sub, "cursor": "c.1"}))
    _, events = session.handle(
        json.dumps({"type": "error", "id": sub, "code": "unavailable"})
    )
    assert events == [] and session.active


@pytest.mark.asyncio
async def test_watch_changes_sends_an_unavailable_subscription_again() -> None:
    received: list[dict] = []

    async def handler(ws: ServerConnection) -> None:
        await ws.send(json.dumps({"type": "ready", "connection": "c"}))
        received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "error", "id": "s1", "code": "unavailable"}))
        received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "subscribed", "id": "s1", "cursor": "c.1"}))
        await ws.send(
            json.dumps({"type": "unsubscribed", "id": "s1", "reason": "access_revoked"})
        )
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        events = [
            event
            async for event in watch_changes(
                base_url=f"http://127.0.0.1:{port}",
                pod_id="P",
                token=lambda: "T",
                table="t",
                max_reconnects=3,
            )
        ]

    assert [type(event).__name__ for event in events] == ["LiveReady", "LiveEnded"]
    assert len(received) == 2 and received[0] == received[1]
