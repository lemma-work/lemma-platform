"""The ``org`` topic's hub over in-memory feeds.

Every collaborator the hub has -- the organization's directory, readers'
standing in pods, the modules' states and relays, the signal channels and the
grants epochs -- is a fake, so these drive the real hub and the real socket
queue and stand in only for the database and Redis behind them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.live.config import LiveSettings
from app.core.live.connection import OrgConnection
from app.core.live.org_feeds import (
    Audience,
    OrgAccess,
    OrgItem,
    OrgPods,
    OrgReader,
    PodItems,
)
from app.core.live.org_feeds import OrgFeeds
from app.core.live.org_hub import OrgHub
from app.core.live.org_readers import Standing
from app.core.live.protocol import CLOSE_FORBIDDEN, LiveAccessDenied, LiveErrorCode
from app.core.live.signals import CatchUp, Signal, SignalHandler

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# -- fakes --------------------------------------------------------------------------


@dataclass
class Directory:
    """Who is in the organization, and the pods its listing shows each."""

    pods: dict[UUID, list[UUID]] = field(default_factory=dict)

    async def pods_for(self, organization_id: UUID, user_id: UUID) -> OrgPods:
        if user_id not in self.pods:
            return OrgPods(access=OrgAccess.FORBIDDEN)
        return OrgPods(access=OrgAccess.MEMBER, pod_ids=list(self.pods[user_id]))


@dataclass
class Grants:
    """Who may open which pod, and what each holds there."""

    opens: set[tuple[UUID, UUID]] = field(default_factory=set)
    holds: set[tuple[UUID, UUID, str]] = field(default_factory=set)
    asked: int = 0


class FakeStanding:
    def __init__(self, grants: Grants) -> None:
        self._grants = grants

    async def opens(self, user_id: UUID, pod_id: UUID) -> bool:
        return (user_id, pod_id) in self._grants.opens

    async def admits(self, user_id: UUID, pod_id: UUID, audience: Audience) -> bool:
        self._grants.asked += 1
        if audience.users is not None and user_id not in audience.users:
            return False
        return audience.permission is None or (
            (user_id, pod_id, audience.permission) in self._grants.holds
        )


def standing_of(grants: Grants) -> Callable[[], asynccontextmanager]:
    @asynccontextmanager
    async def opened() -> AsyncIterator[Standing]:
        yield FakeStanding(grants)

    return opened


@dataclass
class State:
    """One module's state: what holds in each pod, set by the test."""

    name: str = "test.waiting"
    kinds: frozenset[str] = frozenset({"wait"})
    items: dict[UUID, dict[str, OrgItem]] = field(default_factory=dict)
    reads: list[list[UUID]] = field(default_factory=list)
    fail_next: int = 0

    async def read(self, pod_ids: Sequence[UUID]) -> Mapping[UUID, PodItems]:
        if self.fail_next:
            self.fail_next -= 1
            raise OperationalError("select", {}, Exception("database away"))
        self.reads.append(list(pod_ids))
        return {pod: dict(self.items.get(pod, {})) for pod in pod_ids}


def waiting(
    key: str, *, users: set[UUID] | None = None, permission: str | None = None
) -> OrgItem:
    return OrgItem(
        audience=Audience(
            users=frozenset(users) if users is not None else None, permission=permission
        ),
        version="1",
        held={"type": "waiting.started", "id": key},
        ended={"type": "waiting.stopped", "id": key},
    )


@dataclass
class Watch:
    reader: OrgReader
    released: bool = False

    def release(self) -> None:
        self.released = True


@dataclass
class Relay:
    name: str = "test.relay"
    watches: list[Watch] = field(default_factory=list)

    async def watch(self, reader: OrgReader) -> Watch:
        watch = Watch(reader)
        self.watches.append(watch)
        return watch

    def of(self, pod_id: UUID) -> list[Watch]:
        return [watch for watch in self.watches if watch.reader.pod_id == pod_id]


class Channel:
    def __init__(self) -> None:
        self.handlers: dict[str, list[SignalHandler]] = {}
        self.catch_ups: list[CatchUp] = []

    def listen(
        self, kinds: list[str], handler: SignalHandler, catch_up: CatchUp
    ) -> None:
        for kind in kinds:
            self.handlers.setdefault(kind, []).append(handler)
        self.catch_ups.append(catch_up)

    def forget(self, handler: SignalHandler, catch_up: CatchUp) -> None:
        for handlers in self.handlers.values():
            if handler in handlers:
                handlers.remove(handler)
        if catch_up in self.catch_ups:
            self.catch_ups.remove(catch_up)

    def send(self, signal: Signal) -> None:
        for handler in list(self.handlers.get(str(signal.get("k")), ())):
            handler(signal)

    @property
    def heard_by(self) -> int:
        return len(self.catch_ups)


class Listeners:
    def __init__(self) -> None:
        self.pods: dict[UUID, Channel] = {}
        self.orgs: dict[UUID, Channel] = {}

    async def of(self, pod_id: UUID) -> Channel:
        return self.pods.setdefault(pod_id, Channel())

    async def of_org(self, organization_id: UUID) -> Channel:
        return self.orgs.setdefault(organization_id, Channel())

    async def release(self, pod_id: UUID) -> None:
        return None

    async def release_org(self, organization_id: UUID) -> None:
        return None


class Socket:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))

    def events(self) -> list[dict[str, object]]:
        return [
            event
            for frame in self.frames
            if frame["type"] == "events"
            for event in frame["events"]  # type: ignore[union-attr]
        ]

    def of(self, kind: str) -> list[dict[str, object]]:
        return [event for event in self.events() if event["type"] == kind]


async def eventually(condition: Callable[[], bool], *, within: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + within
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


# -- the rig ------------------------------------------------------------------------


@dataclass
class Rig:
    org: UUID = field(default_factory=uuid4)
    directory: Directory = field(default_factory=Directory)
    grants: Grants = field(default_factory=Grants)
    state: State = field(default_factory=State)
    relay: Relay = field(default_factory=Relay)
    listeners: Listeners = field(default_factory=Listeners)
    epochs: dict[UUID, str] = field(default_factory=dict)
    settings: LiveSettings = field(
        default_factory=lambda: LiveSettings(
            live_org_poll_seconds=3600,
            live_authz_check_seconds=0.02,
            live_authz_recheck_seconds=3600,
            live_wake_coalesce_ms=0,
        )
    )
    hub: OrgHub | None = None
    senders: list[asyncio.Task[None]] = field(default_factory=list)

    def build(self) -> OrgHub:
        async def epochs_of(pod_ids: Sequence[UUID]) -> dict[UUID, str | None]:
            return {pod: self.epochs.get(pod, "0") for pod in pod_ids}

        self.hub = OrgHub(
            self.org,
            feeds=OrgFeeds(
                directory=self.directory, relays=(self.relay,), states=(self.state,)
            ),
            standing=standing_of(self.grants),
            listeners=self.listeners,  # type: ignore[arg-type]
            epochs_of=epochs_of,
            settings=self.settings,
            on_idle=lambda _hub: None,
        )
        return self.hub

    def member(self, *pods: UUID, opens: Sequence[UUID] | None = None) -> UUID:
        user = uuid4()
        self.directory.pods[user] = list(pods)
        for pod in pods if opens is None else opens:
            self.grants.opens.add((user, pod))
        return user

    async def subscribe(self, user: UUID) -> tuple[OrgConnection, Socket]:
        socket = Socket()
        connection = OrgConnection(
            organization_id=self.org,
            user_id=user,
            send_text=socket.send_text,
            queue_bytes=1 << 20,
        )
        self.senders.append(asyncio.create_task(connection.run_sender()))
        assert self.hub is not None
        await self.hub.join(connection, "o1")
        return connection, socket

    def signal(self, pod: UUID, kind: str = "wait") -> None:
        self.listeners.pods[pod].send({"k": kind})

    async def close(self) -> None:
        if self.hub is not None:
            await self.hub.stop()
        for task in self.senders:
            task.cancel()


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    made = Rig()
    try:
        yield made
    finally:
        await made.close()


# -- which pods ---------------------------------------------------------------------


async def test_a_reader_covers_only_the_pods_they_may_open(rig: Rig) -> None:
    mine, theirs, listed_only = uuid4(), uuid4(), uuid4()
    user = rig.member(mine, listed_only, opens=[mine])
    rig.member(theirs)
    rig.build()

    _, socket = await rig.subscribe(user)

    await eventually(lambda: bool(socket.of("pods")))
    assert socket.of("pods") == [
        {"type": "pods", "pod_ids": [str(mine)], "more": False}
    ]
    assert [watch.reader.pod_id for watch in rig.relay.watches] == [mine]
    assert set(rig.listeners.pods) == {mine}


async def test_pods_past_the_cap_are_left_out_and_said_to_be(rig: Rig) -> None:
    rig.settings = LiveSettings(live_org_max_pods=2, live_org_poll_seconds=3600)
    pods = [uuid4() for _ in range(3)]
    user = rig.member(*pods)
    rig.build()

    _, socket = await rig.subscribe(user)

    await eventually(lambda: bool(socket.of("pods")))
    assert socket.of("pods") == [
        {"type": "pods", "pod_ids": [str(pods[0]), str(pods[1])], "more": True}
    ]


async def test_someone_outside_the_organization_is_refused(rig: Rig) -> None:
    rig.build()

    with pytest.raises(LiveAccessDenied) as refused:
        await rig.subscribe(uuid4())

    assert refused.value.code is LiveErrorCode.NOT_FOUND
    assert rig.relay.watches == []


async def test_a_pod_lost_is_removed_and_nothing_more_is_said_of_it(rig: Rig) -> None:
    kept, lost = uuid4(), uuid4()
    user = rig.member(kept, lost)
    rig.build()
    _, socket = await rig.subscribe(user)

    rig.grants.opens.discard((user, lost))
    rig.epochs[lost] = "1"

    await eventually(lambda: bool(socket.of("pod.removed")))
    assert socket.of("pod.removed") == [{"type": "pod.removed", "pod_id": str(lost)}]
    assert [watch.released for watch in rig.relay.of(lost)] == [True]
    rig.relay.of(lost)[0].reader.notify([{"type": "inbox.updated"}])
    rig.state.items[lost] = {"w": waiting("w")}
    rig.signal(kept)
    await eventually(lambda: len(rig.state.reads) >= 1)
    assert socket.of("inbox.updated") == []
    assert socket.of("waiting.started") == []


async def test_a_pod_joined_is_added_from_what_holds_there_then(rig: Rig) -> None:
    first, joined = uuid4(), uuid4()
    user = rig.member(first)
    rig.build()
    _, socket = await rig.subscribe(user)
    rig.state.items[joined] = {"old": waiting("old")}

    rig.directory.pods[user].append(joined)
    rig.grants.opens.add((user, joined))
    rig.listeners.orgs[rig.org].send({"k": "pods"})

    await eventually(lambda: bool(socket.of("pod.added")))
    assert socket.of("pod.added") == [
        {"type": "pod.added", "pod_id": str(joined), "more": False}
    ]
    rig.state.items[joined]["new"] = waiting("new")
    rig.signal(joined)
    await eventually(lambda: bool(socket.of("waiting.started")))
    assert socket.of("waiting.started") == [
        {"type": "waiting.started", "id": "new", "pod_id": str(joined)}
    ]


async def test_a_reader_taken_out_of_the_organization_loses_the_socket(
    rig: Rig,
) -> None:
    pod = uuid4()
    user = rig.member(pod)
    rig.build()
    connection, _ = await rig.subscribe(user)

    del rig.directory.pods[user]
    rig.listeners.orgs[rig.org].send({"k": "pods"})

    await eventually(lambda: connection.closed)
    assert connection.close_code == CLOSE_FORBIDDEN
    assert [watch.released for watch in rig.relay.watches] == [True]


# -- what is said -------------------------------------------------------------------


async def test_what_held_at_subscribe_is_not_told_and_what_moves_is(rig: Rig) -> None:
    pod = uuid4()
    user = rig.member(pod)
    rig.state.items[pod] = {"before": waiting("before")}
    rig.build()
    _, socket = await rig.subscribe(user)

    rig.state.items[pod] = {"after": waiting("after")}
    rig.signal(pod)

    await eventually(lambda: len(socket.of("waiting.stopped")) == 1)
    assert socket.of("waiting.started") == [
        {"type": "waiting.started", "id": "after", "pod_id": str(pod)}
    ]
    assert socket.of("waiting.stopped") == [
        {"type": "waiting.stopped", "id": "before", "pod_id": str(pod)}
    ]


async def test_an_item_reaches_only_its_audience(rig: Rig) -> None:
    pod = uuid4()
    assignee, editor, viewer = rig.member(pod), rig.member(pod), rig.member(pod)
    rig.grants.holds.add((editor, pod, "contact.read"))
    rig.build()
    sockets = {
        user: (await rig.subscribe(user))[1] for user in (assignee, editor, viewer)
    }

    rig.state.items[pod] = {
        "form": waiting("form", users={assignee}),
        "contact": waiting("contact", permission="contact.read"),
    }
    rig.signal(pod)

    await eventually(
        lambda: len(rig.state.reads) >= 1 and bool(sockets[editor].events())
    )
    told = {
        user: sorted(str(event["id"]) for event in socket.of("waiting.started"))
        for user, socket in sockets.items()
    }
    assert told == {assignee: ["form"], editor: ["contact"], viewer: []}


async def test_a_reader_gaining_standing_is_told_what_now_holds_for_them(
    rig: Rig,
) -> None:
    pod = uuid4()
    user = rig.member(pod)
    rig.state.items[pod] = {"contact": waiting("contact", permission="contact.read")}
    rig.build()
    _, socket = await rig.subscribe(user)

    rig.grants.holds.add((user, pod, "contact.read"))
    rig.epochs[pod] = "1"

    await eventually(lambda: bool(socket.of("waiting.started")))
    assert socket.of("waiting.started") == [
        {"type": "waiting.started", "id": "contact", "pod_id": str(pod)}
    ]

    rig.grants.holds.discard((user, pod, "contact.read"))
    rig.epochs[pod] = "2"
    await eventually(lambda: bool(socket.of("waiting.stopped")))
    assert socket.of("waiting.stopped") == [
        {"type": "waiting.stopped", "id": "contact", "pod_id": str(pod)}
    ]


async def test_a_signal_reads_only_the_states_and_pod_it_names(rig: Rig) -> None:
    named, quiet = uuid4(), uuid4()
    user = rig.member(named, quiet)
    rig.build()
    await rig.subscribe(user)
    rig.state.reads.clear()

    rig.signal(named, kind="unrelated")
    rig.signal(named)

    await eventually(lambda: bool(rig.state.reads))
    assert rig.state.reads == [[named]]


async def test_a_read_finding_nothing_new_weighs_no_reader(rig: Rig) -> None:
    pod = uuid4()
    users = [rig.member(pod) for _ in range(3)]
    rig.state.items[pod] = {"w": waiting("w", permission="contact.read")}
    rig.build()
    for user in users:
        await rig.subscribe(user)
    rig.state.reads.clear()
    rig.grants.asked = 0
    # The first read after a reader joins is weighed for every reader.
    rig.signal(pod)
    await eventually(lambda: rig.grants.asked == len(users))

    rig.signal(pod)
    await eventually(lambda: len(rig.state.reads) == 2)

    assert rig.grants.asked == len(users)


async def test_one_read_serves_every_reader(rig: Rig) -> None:
    pods = [uuid4(), uuid4()]
    users = [rig.member(*pods) for _ in range(4)]
    rig.build()
    sockets = [(await rig.subscribe(user))[1] for user in users]
    rig.state.reads.clear()

    for pod in pods:
        rig.state.items[pod] = {"w": waiting("w")}
        rig.signal(pod)

    await eventually(
        lambda: all(len(socket.of("waiting.started")) == 2 for socket in sockets)
    )
    assert sum(len(read) for read in rig.state.reads) == len(pods)


async def test_a_read_that_fails_is_made_again_and_nothing_is_lost(rig: Rig) -> None:
    pod = uuid4()
    user = rig.member(pod)
    rig.build()
    _, socket = await rig.subscribe(user)

    rig.state.fail_next = 1
    rig.state.items[pod] = {"w": waiting("w")}
    rig.signal(pod)

    await eventually(lambda: bool(socket.of("waiting.started")), within=4.0)
    assert socket.of("waiting.started") == [
        {"type": "waiting.started", "id": "w", "pod_id": str(pod)}
    ]


async def test_a_relay_speaks_for_its_pod(rig: Rig) -> None:
    pod = uuid4()
    user = rig.member(pod)
    rig.build()
    _, socket = await rig.subscribe(user)

    reader = rig.relay.of(pod)[0].reader
    reader.notify([{"type": "inbox.updated", "channel_id": "c"}])
    reader.stale()

    await eventually(lambda: bool(socket.of("pod.stale")))
    assert socket.of("inbox.updated") == [
        {"type": "inbox.updated", "channel_id": "c", "pod_id": str(pod)}
    ]
    assert socket.of("pod.stale") == [{"type": "pod.stale", "pod_id": str(pod)}]


async def test_unsubscribing_lets_every_pod_go(rig: Rig) -> None:
    pods = [uuid4(), uuid4()]
    user = rig.member(*pods)
    hub = rig.build()
    connection, _ = await rig.subscribe(user)

    connection.offer_unsubscribed("o1", "client")

    assert [watch.released for watch in rig.relay.watches] == [True, True]
    assert hub.covered_pods() == set()
    await eventually(
        lambda: all(channel.heard_by == 0 for channel in rig.listeners.pods.values())
    )
