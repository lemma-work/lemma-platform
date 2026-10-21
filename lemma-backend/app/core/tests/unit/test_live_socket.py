"""The live socket's own frames, queue and dispatch -- with no topic but a fake.

Topics are modules'; what is tested here is what every topic relies on: frames
parsed the same way, refusals answered in place, a slow socket costing itself a
resync per subscription at that subscription's own cursor, and calls limited.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4

import pytest
from pydantic import JsonValue

from app.core.live.connection import LiveConnection
from app.core.live.protocol import (
    CallRequest,
    JsonObject,
    LiveAccessDenied,
    LiveErrorCode,
    LiveRequestInvalid,
    PingRequest,
    SubscribeRequest,
    UnsubscribeRequest,
    parse_request,
)
from app.core.live.socket import LiveDispatcher, accept_and_authenticate
from app.core.live.topics import LiveContribution, LiveRegistry

pytestmark = pytest.mark.unit


class _Sink:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def of(self, kind: str) -> list[dict]:
        return [frame for frame in self.sent if frame["type"] == kind]


async def _drain(connection: LiveConnection) -> None:
    sender = asyncio.create_task(connection.run_sender())
    await asyncio.sleep(0.05)
    connection.close()
    await sender


@dataclass(eq=False)
class _Sub:
    id: str
    active: bool = True
    released: bool = False

    def release(self) -> None:
        self.active = False
        self.released = True


class _Topic:
    name = "fake"

    def __init__(self) -> None:
        self.refuse: Exception | None = None

    async def subscribe(
        self, connection: LiveConnection, request: SubscribeRequest
    ) -> None:
        connection.subscriptions[request.id] = _Sub(request.id)
        if self.refuse is not None:
            raise self.refuse
        connection.offer_subscribed(request.id, request.since or "c0")


class _Echo:
    name = "echo"

    def per_minute(self) -> int:
        return 2

    async def __call__(
        self, connection: LiveConnection, params: JsonObject, *, call_id: str
    ) -> JsonValue:
        if params.get("deny"):
            raise LiveAccessDenied(LiveErrorCode.FORBIDDEN, "no")
        return {"echo": params.get("say")}


def _connection(sink: _Sink, queue_bytes: int = 1 << 20) -> LiveConnection:
    return LiveConnection(
        pod_id=uuid4(),
        user_id=uuid4(),
        send_text=sink.send_text,
        queue_bytes=queue_bytes,
    )


def _dispatcher(topic: _Topic, *, max_subscriptions: int = 2) -> LiveDispatcher:
    registry = LiveRegistry()
    registry.install(LiveContribution(topics=(topic,), calls=(_Echo(),)))
    return LiveDispatcher(max_subscriptions=max_subscriptions, registry=registry)


def test_parses_every_client_frame() -> None:
    subscribe = parse_request(
        '{"op":"subscribe","id":"s1","topic":"channel","channel_id":"c","since":"3:2"}'
    )
    assert subscribe == SubscribeRequest(
        id="s1", topic="channel", since="3:2", params={"channel_id": "c"}
    )
    # A frame without a topic is the records topic, as the socket always was.
    assert parse_request('{"op":"subscribe","id":"s"}') == SubscribeRequest(id="s")
    assert parse_request('{"op":"unsubscribe","id":"s1"}') == UnsubscribeRequest(
        op="unsubscribe", id="s1"
    )
    assert parse_request('{"op":"ping"}') == PingRequest(op="ping")
    assert parse_request(
        '{"op":"call","id":"c1","method":"typing","params":{"channel_id":"x"}}'
    ) == CallRequest(op="call", id="c1", method="typing", params={"channel_id": "x"})


@pytest.mark.parametrize(
    ("raw", "request_id"),
    [
        ("not json", None),
        ('{"op":"subscribe"}', None),
        ('{"op":"subscribe","id":""}', ""),
        ('{"op":"call","id":"c1"}', "c1"),
        ('{"op":"dance"}', None),
    ],
)
def test_refuses_what_it_cannot_act_on(raw: str, request_id: str | None) -> None:
    with pytest.raises(LiveRequestInvalid) as refused:
        parse_request(raw)
    assert refused.value.request_id == request_id


@pytest.mark.asyncio
async def test_refusals_are_answered_in_place_and_keep_the_socket() -> None:
    sink, topic = _Sink(), _Topic()
    connection = _connection(sink)
    dispatch = _dispatcher(topic)
    await dispatch.handle(connection, '{"op":"subscribe","id":"a","topic":"fake"}')
    await dispatch.handle(connection, '{"op":"subscribe","id":"a","topic":"fake"}')
    await dispatch.handle(connection, '{"op":"subscribe","id":"b","topic":"nope"}')
    topic.refuse = LiveAccessDenied(LiveErrorCode.NOT_FOUND, "missing")
    await dispatch.handle(connection, '{"op":"subscribe","id":"c","topic":"fake"}')
    topic.refuse = None
    await dispatch.handle(connection, '{"op":"subscribe","id":"d","topic":"fake"}')
    await dispatch.handle(connection, '{"op":"subscribe","id":"e","topic":"fake"}')
    await dispatch.handle(connection, "x" * (17 * 1024))
    await dispatch.handle(connection, '{"op":"ping"}')
    await dispatch.handle(connection, '{"op":"unsubscribe","id":"a"}')
    await _drain(connection)

    errors = {frame["id"]: frame["code"] for frame in sink.of("error")}
    assert errors == {
        "a": "invalid",
        "b": "invalid",
        "c": "not_found",
        "e": "limit",
        None: "invalid",
    }
    assert [frame["id"] for frame in sink.of("subscribed")] == ["a", "d"]
    assert sink.of("pong") == [{"type": "pong"}]
    assert sink.of("unsubscribed") == [
        {"type": "unsubscribed", "id": "a", "reason": "client"}
    ]


@pytest.mark.asyncio
async def test_a_refused_subscription_is_let_go() -> None:
    sink, topic = _Sink(), _Topic()
    connection = _connection(sink)
    topic.refuse = OSError("database away")
    await _dispatcher(topic).handle(
        connection, '{"op":"subscribe","id":"a","topic":"fake"}'
    )
    assert "a" not in connection.subscriptions
    await _drain(connection)
    assert sink.of("error")[0]["code"] == "unavailable"


@pytest.mark.asyncio
async def test_calls_answer_on_their_id_and_are_limited_per_minute() -> None:
    sink = _Sink()
    connection = _connection(sink)
    dispatch = _dispatcher(_Topic())
    for n in range(3):
        await dispatch.handle(
            connection,
            json.dumps(
                {"op": "call", "id": f"c{n}", "method": "echo", "params": {"say": n}}
            ),
        )
    await dispatch.handle(connection, '{"op":"call","id":"x","method":"nope"}')
    await _drain(connection)
    assert sink.of("result") == [
        {"type": "result", "id": "c0", "result": {"echo": 0}},
        {"type": "result", "id": "c1", "result": {"echo": 1}},
    ]
    assert {f["id"]: f["code"] for f in sink.of("error")} == {
        "c2": "limit",
        "x": "invalid",
    }


@pytest.mark.asyncio
async def test_overflow_resyncs_each_subscription_at_its_own_cursor() -> None:
    sink = _Sink()
    connection = _connection(sink, queue_bytes=4096)
    for name in ("records", "channel"):
        connection.subscriptions[name] = _Sub(name)
    pad = "x" * 500
    for n in range(10):
        connection.offer_events("records", f"r{n}", [json.dumps({"pad": pad})])
        connection.offer_events("channel", f"{n}:{n}", [json.dumps({"pad": pad})])
    assert connection.overflows >= 1
    assert connection.queued_bytes <= 4096
    await _drain(connection)
    resyncs = {frame["id"]: frame for frame in sink.of("resync")}
    assert set(resyncs) == {"records", "channel"}
    assert all(frame["reason"] == "overflow" for frame in resyncs.values())
    # Each at or after anything of its own that was dropped, in its own cursor.
    assert resyncs["records"]["cursor"].startswith("r")
    assert ":" in resyncs["channel"]["cursor"]


@pytest.mark.asyncio
async def test_closing_lets_every_subscription_go() -> None:
    sink = _Sink()
    connection = _connection(sink)
    subs = [_Sub("a"), _Sub("b")]
    for sub in subs:
        connection.subscriptions[sub.id] = sub
    connection.close()
    assert all(sub.released for sub in subs)
    assert connection.subscriptions == {}


@pytest.mark.asyncio
async def test_accept_race_and_missing_token_never_close_before_accept() -> None:
    class _Socket:
        def __init__(self, *, accept_fails: bool) -> None:
            self.calls: list[tuple[str, object]] = []
            self.accept_fails = accept_fails
            self.headers: dict[str, str] = {}
            self.cookies: dict[str, str] = {}
            self.query_params: dict[str, str] = {}

        async def accept(self) -> None:
            self.calls.append(("accept", None))
            if self.accept_fails:
                raise RuntimeError("client went away")

        async def close(self, code: int, reason: str = "") -> None:
            self.calls.append(("close", code))

    raced = _Socket(accept_fails=True)
    assert await accept_and_authenticate(raced) is None  # type: ignore[arg-type]
    assert raced.calls == [("accept", None)]

    anonymous = _Socket(accept_fails=False)
    assert await accept_and_authenticate(anonymous) is None  # type: ignore[arg-type]
    assert anonymous.calls == [("accept", None), ("close", 4401)]


def test_a_name_two_modules_claim_stops_the_process() -> None:
    registry = LiveRegistry()
    registry.install(LiveContribution(topics=(_Topic(),)))
    # The same module installing again (another app in this process) replaces.
    registry.install(LiveContribution(topics=(_Topic(),)))

    class _Other:
        name = "fake"

        async def subscribe(
            self, connection: LiveConnection, request: SubscribeRequest
        ) -> None: ...

    with pytest.raises(RuntimeError):
        registry.install(LiveContribution(topics=(_Other(),)))
