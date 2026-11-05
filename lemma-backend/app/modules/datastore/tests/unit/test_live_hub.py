"""The pod hub over an in-memory change log.

Every collaborator the hub has -- the log, table access, the wake channel, the
authorization epoch -- is injected, so these tests drive the real hub and the
real socket queue and stand in only for the database and Redis behind them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from app.core.live.config import LiveSettings
from app.core.live.connection import LiveConnection
from app.core.live.protocol import (
    LiveAccessDenied,
    LiveErrorCode,
    LiveUnavailable as LiveHubUnavailable,
    SubscribeRequest as CoreSubscribeRequest,
)
from app.core.live.socket import LiveDispatcher
from app.core.live.topics import LiveContribution, LiveRegistry
from app.modules.datastore.domain.live import (
    ChangePosition,
    LiveChange,
    LiveCursorExpired,
    LiveOp,
    LivePage,
    LiveTable,
    RowPayload,
    SubscribeRequest,
    UnsubscribedReason,
    records_request,
)
from app.modules.datastore.services.live import hub_start
from app.modules.datastore.services.live.hub import PodHub
from app.modules.datastore.services.live.registry import LiveHubRegistry
from app.modules.datastore.services.live.subscription import LegacyFrames

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _cursor(position: ChangePosition) -> str:
    return f"t.{position[0]}.{position[1]}"


@dataclass
class MemoryLog:
    """A pod change log: positions ``(1, n)``; ``floor`` is what retention kept."""

    changes: list[LiveChange] = field(default_factory=list)
    rows: dict[str, RowPayload] = field(default_factory=dict)
    floor: ChangePosition = (0, 0)
    reads: int = 0
    row_fetches: int = 0

    def write(
        self,
        table_id: UUID,
        op: LiveOp,
        record_id: str,
        *,
        owner: UUID | None = None,
        row: RowPayload | None = None,
    ) -> None:
        position = (1, len(self.changes) + 1)
        self.changes.append(LiveChange(position, table_id, op, record_id, owner))
        if op == "delete":
            self.rows.pop(record_id, None)
        else:
            self.rows[record_id] = row or {"id": record_id}


class MemoryFeed:
    def __init__(self, log: MemoryLog) -> None:
        self.log = log

    async def horizon(self, pod_id: UUID) -> str:
        return _cursor(self.log.changes[-1].position if self.log.changes else (0, 0))

    async def read(
        self, pod_id: UUID, cursor: str, *, table_ids: Sequence[UUID] | None, limit: int
    ) -> LivePage:
        self.log.reads += 1
        after = self.position(cursor)
        if after < self.log.floor:
            raise LiveCursorExpired(cursor)
        found = [
            change
            for change in self.log.changes
            if change.position > after
            and (table_ids is None or change.table_id in table_ids)
        ][:limit]
        more = len(found) == limit
        if more:
            next_position = found[-1].position
        else:
            head = self.log.changes[-1].position if self.log.changes else (0, 0)
            next_position = max(after, head)
        return LivePage(tuple(found), _cursor(next_position), more)

    async def rows(
        self, pod_id: UUID, table_id: UUID, record_ids: Sequence[str]
    ) -> dict[str, RowPayload]:
        self.log.row_fetches += 1
        return {rid: self.log.rows[rid] for rid in record_ids if rid in self.log.rows}

    def position(self, cursor: str) -> ChangePosition:
        parts = cursor.split(".")
        if len(parts) != 3 or parts[0] != "t":
            raise LiveCursorExpired(cursor)
        return (int(parts[1]), int(parts[2]))


class MemoryAccess:
    def __init__(self, tables: dict[str, UUID]) -> None:
        self.tables = tables
        #: user -> readable table names; absent user reads everything.
        self.readable: dict[UUID, set[str]] = {}

    def _can(self, user_id: UUID, name: str) -> bool:
        allowed = self.readable.get(user_id)
        return allowed is None or name in allowed

    async def table(self, user_id: UUID, pod_id: UUID, table_name: str) -> LiveTable:
        if table_name not in self.tables:
            raise LiveAccessDenied(LiveErrorCode.NOT_FOUND, "no such table")
        if not self._can(user_id, table_name):
            raise LiveAccessDenied(LiveErrorCode.FORBIDDEN, "no access")
        return LiveTable(self.tables[table_name], table_name)

    async def readable_tables(self, user_id: UUID, pod_id: UUID) -> list[LiveTable]:
        return [
            LiveTable(tid, name)
            for name, tid in self.tables.items()
            if self._can(user_id, name)
        ]

    async def still_readable(
        self, user_id: UUID, pod_id: UUID, table_ids: Sequence[UUID]
    ) -> dict[UUID, str | None]:
        names = {tid: name for name, tid in self.tables.items()}
        return {
            tid: None
            if tid in names and self._can(user_id, names[tid])
            else UnsubscribedReason.ACCESS_REVOKED.value
            for tid in table_ids
        }


class MemoryChannel:
    def __init__(self) -> None:
        self.queues: list[asyncio.Queue[str]] = []

    async def subscriber_count(self, channel: str) -> int:
        del channel
        return len(self.queues)

    async def publish(self, channel: str, message: object) -> None:
        for queue in self.queues:
            queue.put_nowait(str(message))

    @asynccontextmanager
    async def subscribe(
        self, channels: Sequence[str]
    ) -> AsyncIterator[AsyncIterator[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self.queues.append(queue)

        async def messages() -> AsyncIterator[str]:
            while True:
                yield await queue.get()

        try:
            yield messages()
        finally:
            self.queues.remove(queue)


class Socket:
    """Collects what a connection sends; ``stall`` makes sends hang."""

    def __init__(self, *, stall: bool = False) -> None:
        self.frames: list[dict] = []
        self.stall = stall

    async def send_text(self, text: str) -> None:
        if self.stall:
            await asyncio.Event().wait()
        self.frames.append(json.loads(text))

    def of(self, kind: str, sub: str | None = None) -> list[dict]:
        return [
            f
            for f in self.frames
            if f["type"] == kind and (sub is None or f.get("id") == sub)
        ]


class RigTopic:
    """The ``records`` topic over the rig's own hubs, not the process's."""

    name = "records"

    def __init__(self, registry: LiveHubRegistry) -> None:
        self.registry = registry

    async def subscribe(
        self, connection: LiveConnection, request: CoreSubscribeRequest
    ) -> None:
        hub = await self.registry.hub(connection.pod_id)
        await hub.subscribe(connection, records_request(request))


@dataclass
class Rig:
    log: MemoryLog
    access: MemoryAccess
    channel: MemoryChannel
    epoch: list[str]
    registry: LiveHubRegistry
    pod_id: UUID
    senders: list[asyncio.Task[None]] = field(default_factory=list)

    async def hub(self) -> PodHub:
        return await self.registry.hub(self.pod_id)

    async def connect(
        self,
        user_id: UUID | None = None,
        *,
        stall: bool = False,
        queue_bytes: int = 1 << 20,
    ) -> tuple[LiveConnection, Socket]:
        socket = Socket(stall=stall)
        connection = LiveConnection(
            pod_id=self.pod_id,
            user_id=user_id or uuid4(),
            send_text=socket.send_text,
            queue_bytes=queue_bytes,
        )
        self.senders.append(asyncio.create_task(connection.run_sender()))
        return connection, socket

    async def subscribe(self, connection: LiveConnection, **request: object) -> None:
        """Through the socket's own dispatch, as a client's frame arrives."""
        topics = LiveRegistry()
        topics.install(LiveContribution(topics=(RigTopic(self.registry),)))
        await LiveDispatcher(max_subscriptions=3, registry=topics).handle(
            connection, json.dumps({"op": "subscribe", "topic": "records", **request})
        )

    async def publish(self) -> None:
        await self.channel.publish(f"pod:{self.pod_id}:changes", "wake")

    async def settle(self) -> None:
        await asyncio.sleep(0.4)


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    tables = {"tickets": uuid4(), "comments": uuid4(), "orders": uuid4()}
    log = MemoryLog()
    access = MemoryAccess(tables)
    channel = MemoryChannel()
    epoch = ["1"]

    async def epoch_of(pod_id: UUID) -> str | None:
        return epoch[0]

    async def channel_factory() -> MemoryChannel:
        return channel

    settings = LiveSettings(
        live_wake_coalesce_ms=0,
        live_idle_poll_seconds=5.0,
        live_authz_check_seconds=0.05,
        live_hub_linger_seconds=0.05,
        live_socket_queue_bytes=64 * 1024,
    )
    registry = LiveHubRegistry(
        feed_factory=lambda: MemoryFeed(log),
        access_factory=lambda: access,
        channel_factory=channel_factory,  # type: ignore[arg-type]
        epoch_of=epoch_of,
        settings=settings,
    )
    rig = Rig(log, access, channel, epoch, registry, uuid4())
    yield rig
    for task in rig.senders:
        task.cancel()
    await registry.close()


async def test_bulk_write_resyncs_only_the_subscription_it_floods(rig: Rig) -> None:
    connection, socket = await rig.connect()
    for sub, table in (("a", "tickets"), ("b", "comments"), ("c", "orders")):
        await rig.subscribe(connection, id=sub, table=table)
    reads_before = rig.log.reads

    for n in range(10_000):
        rig.log.write(rig.access.tables["tickets"], "insert", f"t{n}")
    rig.log.write(
        rig.access.tables["comments"], "insert", "c1", row={"id": "c1", "body": "hi"}
    )
    rig.log.write(rig.access.tables["orders"], "update", "o1", row={"id": "o1", "n": 2})
    await rig.publish()
    await rig.settle()

    assert [f["reason"] for f in socket.of("resync", "a")] == ["too_many"]
    assert socket.of("changes", "a") == []
    [comments] = socket.of("changes", "b")
    assert comments["changes"] == [
        {
            "op": "insert",
            "table": "comments",
            "record_id": "c1",
            "row": {"id": "c1", "body": "hi"},
        }
    ]
    [orders] = socket.of("changes", "c")
    assert orders["changes"][0]["row"] == {"id": "o1", "n": 2}
    # One hub read pass over the whole burst, not one per subscription or row,
    # and no row fetch for the flooded table.
    assert rig.log.reads - reads_before <= 21
    assert rig.log.row_fetches == 2


async def test_rls_rows_reach_only_their_owner(rig: Rig) -> None:
    alice, bob = uuid4(), uuid4()
    alice_conn, alice_socket = await rig.connect(alice)
    bob_conn, bob_socket = await rig.connect(bob)
    await rig.subscribe(alice_conn, id="s", table="tickets")
    await rig.subscribe(bob_conn, id="s", table="tickets")

    tickets = rig.access.tables["tickets"]
    rig.log.write(tickets, "insert", "mine", owner=alice)
    rig.log.write(tickets, "insert", "shared", owner=None)
    await rig.publish()
    await rig.settle()

    def ids(socket: Socket) -> set[str]:
        return {c["record_id"] for f in socket.of("changes") for c in f["changes"]}

    assert ids(alice_socket) == {"mine", "shared"}
    assert ids(bob_socket) == {"shared"}


async def test_resume_from_since_is_gap_free_and_not_duplicated(rig: Rig) -> None:
    tickets = rig.access.tables["tickets"]
    first, first_socket = await rig.connect()
    await rig.subscribe(first, id="s", table="tickets")
    rig.log.write(tickets, "insert", "r1")
    await rig.publish()
    await rig.settle()
    cursor = first_socket.of("changes", "s")[-1]["cursor"]

    # The client drops; three changes land while it is away.
    first.close()
    for rid in ("r2", "r3", "r4"):
        rig.log.write(tickets, "insert", rid)
    await rig.publish()
    await rig.settle()

    again, socket = await rig.connect()
    await rig.subscribe(again, id="s", table="tickets", since=cursor)
    rig.log.write(tickets, "insert", "r5")
    await rig.publish()
    await rig.settle()

    seen = [c["record_id"] for f in socket.of("changes", "s") for c in f["changes"]]
    assert seen == ["r2", "r3", "r4", "r5"]
    assert socket.frames[0]["type"] == "subscribed"


async def test_expired_or_foreign_since_answers_resync(rig: Rig) -> None:
    rig.log.write(rig.access.tables["tickets"], "insert", "r1")
    rig.log.floor = (1, 1)
    connection, socket = await rig.connect()
    await rig.subscribe(connection, id="old", table="tickets", since="t.0.0")
    await rig.subscribe(
        connection, id="redis", table="tickets", since="1712345678901-0"
    )
    await rig.settle()

    assert [f["reason"] for f in socket.of("resync")] == [
        "cursor_expired",
        "cursor_expired",
    ]
    assert {f["id"] for f in socket.of("subscribed")} == {"old", "redis"}


async def test_slow_socket_gets_resync_and_does_not_hold_up_the_hub(rig: Rig) -> None:
    tickets = rig.access.tables["tickets"]
    slow, _ = await rig.connect(stall=True, queue_bytes=64 * 1024)
    fast, fast_socket = await rig.connect()
    await rig.subscribe(slow, id="s", table="tickets")
    await rig.subscribe(fast, id="s", table="tickets")

    for burst in range(15):
        for n in range(200):
            rig.log.write(
                tickets,
                "insert",
                f"{burst}-{n}",
                row={"id": f"{burst}-{n}", "pad": "x" * 200},
            )
        await rig.publish()
        await rig.settle()

    delivered = sum(len(f["changes"]) for f in fast_socket.of("changes", "s"))
    assert delivered == 3000
    assert slow.overflows >= 1
    assert slow.queued_bytes <= 64 * 1024


async def test_revoked_table_access_ends_the_subscription(rig: Rig) -> None:
    user = uuid4()
    connection, socket = await rig.connect(user)
    await rig.subscribe(connection, id="t", table="tickets")
    await rig.subscribe(connection, id="o", table="orders")
    await rig.subscribe(connection, id="all")

    rig.access.readable[user] = {"orders"}
    rig.epoch[0] = "2"
    await rig.settle()

    assert socket.of("unsubscribed", "t") == [
        {"type": "unsubscribed", "id": "t", "reason": "access_revoked"}
    ]
    assert socket.of("unsubscribed", "o") == []
    assert [f["reason"] for f in socket.of("resync", "all")] == ["permission_changed"]
    assert set(connection.subscriptions) == {"o", "all"}

    rig.log.write(rig.access.tables["tickets"], "insert", "hidden")
    rig.log.write(rig.access.tables["orders"], "insert", "visible")
    await rig.publish()
    await rig.settle()
    seen = {c["record_id"] for f in socket.of("changes") for c in f["changes"]}
    assert seen == {"visible"}


async def test_subscribe_refusals_keep_the_socket_open(rig: Rig) -> None:
    user = uuid4()
    rig.access.readable[user] = {"tickets"}
    connection, socket = await rig.connect(user)
    await rig.subscribe(connection, id="a", table="orders")
    await rig.subscribe(connection, id="b", table="nope")
    await rig.subscribe(connection, id="c", table="tickets")
    await rig.subscribe(connection, id="c", table="tickets")
    await rig.subscribe(connection, id="d", table="tickets")
    await rig.subscribe(connection, id="e", table="tickets")
    await rig.subscribe(connection, id="f", table="tickets")
    await rig.settle()

    codes = {f["id"]: f["code"] for f in socket.of("error")}
    assert codes == {"a": "forbidden", "b": "not_found", "c": "invalid", "f": "limit"}
    assert set(connection.subscriptions) == {"c", "d", "e"}


async def test_one_record_appears_once_per_batch_at_its_latest_state(rig: Rig) -> None:
    connection, socket = await rig.connect()
    await rig.subscribe(
        connection, id="s", table="tickets", ops=["insert", "update", "delete"]
    )
    tickets = rig.access.tables["tickets"]
    rig.log.write(tickets, "insert", "r1", row={"id": "r1", "v": 1})
    rig.log.write(tickets, "update", "r1", row={"id": "r1", "v": 2})
    rig.log.write(tickets, "insert", "r2")
    rig.log.write(tickets, "delete", "r2")
    await rig.publish()
    await rig.settle()

    [frame] = socket.of("changes", "s")
    assert frame["changes"] == [
        {
            "op": "insert",
            "table": "tickets",
            "record_id": "r1",
            "row": {"id": "r1", "v": 2},
        },
        {"op": "delete", "table": "tickets", "record_id": "r2", "row": None},
    ]


async def test_ops_filter_and_unsubscribe(rig: Rig) -> None:
    connection, socket = await rig.connect()
    await rig.subscribe(connection, id="s", table="tickets", ops=["delete"])
    tickets = rig.access.tables["tickets"]
    rig.log.write(tickets, "insert", "r1")
    rig.log.write(tickets, "delete", "r1")
    await rig.publish()
    await rig.settle()
    assert [c["op"] for f in socket.of("changes") for c in f["changes"]] == ["delete"]

    connection.offer_unsubscribed("s", "client")
    rig.log.write(tickets, "delete", "r9")
    await rig.publish()
    await rig.settle()
    assert len(socket.of("changes")) == 1


async def test_idle_hub_is_reaped_and_a_new_one_starts_at_now(rig: Rig) -> None:
    connection, _ = await rig.connect()
    await rig.subscribe(connection, id="s", table="tickets")
    connection.close()
    rig.log.write(rig.access.tables["tickets"], "insert", "while-nobody-watched")
    await asyncio.sleep(0.3)
    assert rig.pod_id not in rig.registry.hubs()

    again, socket = await rig.connect()
    await rig.subscribe(again, id="s", table="tickets")
    rig.log.write(rig.access.tables["tickets"], "insert", "after")
    await rig.publish()
    await rig.settle()
    assert [c["record_id"] for f in socket.of("changes") for c in f["changes"]] == [
        "after"
    ]


async def test_legacy_connection_frames_one_change_per_frame(rig: Rig) -> None:
    socket = Socket()
    connection = LiveConnection(
        pod_id=rig.pod_id,
        user_id=uuid4(),
        send_text=socket.send_text,
        queue_bytes=1 << 20,
        frames=LegacyFrames(),
    )
    hub = await rig.hub()
    rig.senders.append(asyncio.create_task(connection.run_sender()))
    await hub.subscribe(
        connection,
        SubscribeRequest(id="changes", table="tickets", since="1712-0"),
        legacy=True,
    )
    rig.log.write(
        rig.access.tables["tickets"], "insert", "r1", row={"id": "r1", "body": "b"}
    )
    await rig.publish()
    await rig.settle()

    # Subscribed and the expired-cursor resync both read as `ready` to it.
    assert [f["type"] for f in socket.frames] == [
        "ready",
        "ready",
        "datastore.record.insert",
    ]
    change = socket.frames[-1]
    assert change["operation"] == "INSERT"
    assert change["payload"] == {"id": "r1", "body": "b"}
    assert change["stream_id"] == change["cursor"]


async def test_an_unexpected_start_failure_refuses_the_socket_and_the_next_one_starts(
    rig: Rig, monkeypatch
) -> None:
    """A start that fails in a way nobody planned for must still release the
    sockets waiting on it, and must not refuse the pod until the hub is reaped."""
    hub = await rig.hub()
    real_horizon = hub._feed.horizon
    calls = 0

    async def flaky_horizon(pod_id: UUID) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("not one of the start errors anyone expected")
        return await real_horizon(pod_id)

    monkeypatch.setattr(hub._feed, "horizon", flaky_horizon)

    with pytest.raises(LiveHubUnavailable):
        await asyncio.wait_for(hub.ensure_started(), timeout=2)
    await asyncio.wait_for(hub.ensure_started(), timeout=2)
    assert hub.cursor is not None


async def test_a_start_that_never_finishes_or_is_stopped_does_not_hang_the_socket(
    rig: Rig, monkeypatch
) -> None:
    hub = await rig.hub()
    stuck = asyncio.Event()

    async def stuck_horizon(pod_id: UUID) -> str:
        await stuck.wait()
        return ""

    monkeypatch.setattr(hub._feed, "horizon", stuck_horizon)
    monkeypatch.setattr(hub_start, "START_TIMEOUT", 0.2)
    with pytest.raises(LiveHubUnavailable):
        await asyncio.wait_for(hub.ensure_started(), timeout=2)

    # Stopped mid-start (the reaper, a shutdown): the waiter hears at once.
    waiting = asyncio.create_task(hub.ensure_started())
    await asyncio.sleep(0.05)
    await hub.stop()
    with pytest.raises(LiveHubUnavailable):
        await asyncio.wait_for(waiting, timeout=2)


async def test_a_waiter_reads_the_start_it_joined_not_a_retry_after_it() -> None:
    """A retry begun before an earlier start's waiter resumed used to wipe the
    failure that waiter woke for, so it reported a start that never happened."""
    start = hub_start.HubStart("live-hub-test")
    gate = asyncio.Event()

    async def fails(attempt: hub_start.StartAttempt) -> None:
        await gate.wait()
        attempt.failed(RuntimeError("the first start failed"))

    async def never(attempt: hub_start.StartAttempt) -> None:
        await asyncio.Event().wait()

    start.launch(fails)
    waiter = asyncio.create_task(start.until_started())
    await asyncio.sleep(0)
    gate.set()
    while start.task is not None and not start.task.done():
        await asyncio.sleep(0)
    start.launch(never)  # the retry, before the first waiter has run

    with pytest.raises(LiveHubUnavailable):
        await asyncio.wait_for(waiter, timeout=2)
    start.take()


async def test_a_start_stopped_before_it_began_releases_its_waiters_at_once(
    monkeypatch,
) -> None:
    monkeypatch.setattr(hub_start, "START_TIMEOUT", 30.0)
    start = hub_start.HubStart("live-hub-test")

    async def never(attempt: hub_start.StartAttempt) -> None:
        await asyncio.Event().wait()

    start.launch(never)
    waiter = asyncio.create_task(start.until_started())
    await asyncio.sleep(0)
    task = start.take()  # stopped before its first step ran
    assert task is not None
    task.cancel()

    with pytest.raises(LiveHubUnavailable):
        await asyncio.wait_for(waiter, timeout=1)
