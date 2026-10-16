"""Client frames, change merging, and the socket's bounded queue."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.modules.datastore.api.live_socket import accept_and_authenticate
from app.modules.datastore.domain.live import (
    EncodedChange,
    LiveChange,
    LiveRequestInvalid,
    PingRequest,
    SubscribeRequest,
    UnsubscribeRequest,
    merge_change,
    parse_request,
)
from app.modules.datastore.services.live.connection import (
    FRAME_BYTES,
    LiveConnection,
    LiveSubscription,
)

pytestmark = pytest.mark.unit


def test_parses_the_three_client_frames() -> None:
    subscribe = parse_request(
        '{"op":"subscribe","id":"s1","topic":"records","table":"tickets",'
        '"ops":["insert"],"since":"c1.abc"}'
    )
    assert subscribe == SubscribeRequest(
        op="subscribe", id="s1", table="tickets", ops=("insert",), since="c1.abc"
    )
    assert parse_request('{"op":"unsubscribe","id":"s1"}') == UnsubscribeRequest(
        op="unsubscribe", id="s1"
    )
    assert parse_request('{"op":"ping"}') == PingRequest(op="ping")


@pytest.mark.parametrize(
    ("raw", "subscription_id"),
    [
        ("not json", None),
        ('{"op":"subscribe"}', None),
        ('{"op":"subscribe","id":"s1","topic":"files"}', "s1"),
        ('{"op":"subscribe","id":"s1","ops":["upsert"]}', "s1"),
        ('{"op":"subscribe","id":"s1","table":"t","when":{"s":{"eq":"open"}}}', "s1"),
        ('{"op":"subscribe","id":"s1","when":{"status":"open"}}', "s1"),
        (
            '{"op":"subscribe","id":"s1","table":"t","when":{"s":{"changed":true}}}',
            "s1",
        ),
        ('{"op":"subscribe","id":"s1","table":"t","when":{"s":{"to":"x"}}}', "s1"),
        ('{"op":"subscribe","id":"s1","table":"t","when":{}}', "s1"),
        ('{"op":"dance"}', None),
    ],
)
def test_refuses_what_it_cannot_act_on(raw: str, subscription_id: str | None) -> None:
    with pytest.raises(LiveRequestInvalid) as refused:
        parse_request(raw)
    assert refused.value.subscription_id == subscription_id


def test_merge_keeps_one_entry_per_record_at_its_latest_state() -> None:
    table = uuid4()

    def change(op: str, seq: int) -> LiveChange:
        return LiveChange((1, seq), table, op, "r", None)  # type: ignore[arg-type]

    insert_then_update = merge_change(
        merge_change(None, change("insert", 1)), change("update", 2)
    )
    assert (insert_then_update.op, insert_then_update.change.position) == (
        "insert",
        (1, 2),
    )
    assert merge_change(insert_then_update, change("delete", 3)).op == "delete"
    assert (
        merge_change(merge_change(None, change("delete", 1)), change("insert", 2)).op
        == "insert"
    )


class _Sink:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


async def _drain(connection: LiveConnection) -> None:
    sender = asyncio.create_task(connection.run_sender())
    await asyncio.sleep(0.05)
    assert connection.queued_bytes == 0
    connection.close()
    await sender


@pytest.mark.asyncio
async def test_overflow_replaces_queued_changes_with_one_resync_each() -> None:
    sink = _Sink()
    connection = LiveConnection(
        pod_id=uuid4(), user_id=uuid4(), send_text=sink.send_text, queue_bytes=4096
    )
    subs = {
        name: LiveSubscription(name, connection, {uuid4(): name}, False, None, (0, 0))
        for name in ("a", "b")
    }
    connection.subscriptions.update(subs)
    connection.offer('{"type":"ready"}')
    row = {"pad": "x" * 500}
    for n in range(10):
        for name, sub in subs.items():
            connection.offer_changes(
                sub,
                f"c{n}",
                [EncodedChange("insert", name, f"{name}{n}", row, None)],
                previous_cursor=f"c{n - 1}",
            )

    assert connection.overflows >= 1
    assert connection.queued_bytes <= 4096
    await _drain(connection)
    kinds = [frame["type"] for frame in sink.sent]
    assert kinds[0] == "ready"
    resyncs = [frame for frame in sink.sent if frame["type"] == "resync"]
    # At most one pending resync per subscription, and each names a cursor at
    # or after every change frame it replaced.
    assert sorted(frame["id"] for frame in resyncs) == ["a", "b"]
    assert all(frame["reason"] == "overflow" for frame in resyncs)


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


@pytest.mark.asyncio
async def test_a_large_batch_is_split_and_only_its_last_frame_moves_the_cursor() -> (
    None
):
    sink = _Sink()
    connection = LiveConnection(
        pod_id=uuid4(), user_id=uuid4(), send_text=sink.send_text, queue_bytes=1 << 20
    )
    sub = LiveSubscription("s", connection, {uuid4(): "t"}, False, None, (0, 0))
    connection.subscriptions["s"] = sub
    row = {"pad": "x" * 10_000}
    batch = [EncodedChange("update", "t", f"r{n}", row, None) for n in range(100)]
    connection.offer_changes(sub, "after", batch, previous_cursor="before")
    await _drain(connection)

    assert len(sink.sent) > 1
    assert all(len(json.dumps(frame)) <= FRAME_BYTES + 1024 for frame in sink.sent)
    assert [frame["cursor"] for frame in sink.sent] == ["before"] * (
        len(sink.sent) - 1
    ) + ["after"]
    assert sum(len(frame["changes"]) for frame in sink.sent) == 100


def test_when_admits_by_the_current_row_and_always_passes_a_delete() -> None:
    request = parse_request(
        '{"op":"subscribe","id":"s1","table":"tickets",'
        '"when":{"status":{"in":["open","new"]},"owner":{"not_equals":null}}}'
    )
    assert isinstance(request, SubscribeRequest)
    conditions = request.conditions()
    assert conditions is not None
    assert conditions.admit("insert", {"status": "open", "owner": "a"})
    assert conditions.admit("update", {"status": "new", "owner": "b"})
    assert not conditions.admit("update", {"status": "closed", "owner": "a"})
    assert not conditions.admit("insert", {"status": "open", "owner": None})
    assert not conditions.admit("insert", {"owner": "a"})
    assert conditions.admit("delete", None)
