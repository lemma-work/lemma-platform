"""The channels hub's loop outlives a fault, starts once however many first
subscribers race to start it, re-checks access until a re-check succeeds, and
the answer relay keeps what a reader joining mid-answer is sent.

Nothing here reads Postgres or Redis: the hub's unit of work does nothing, its
read is a stand-in that fails on cue, and the relay's stream is a queue.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.live.config import LiveSettings
from app.core.live.protocol import JsonObject, LiveUnavailable
from app.core.live.signals import SignalListeners
from app.core.live.topics import LiveRegistry
from app.modules.channels.services.live.hub import ChannelsHub
from app.modules.channels.services.live.instruments import backoff
from app.modules.channels.services.live.relay import (
    SNAPSHOT_CHARS,
    Answering,
    AnswerRelays,
)

pytestmark = pytest.mark.unit


class _Session:
    async def scalar(self, _statement: object) -> datetime:
        return datetime.now(timezone.utc)


class _UnitOfWork:
    """What a hub with nobody watching reads through: the database's clock."""

    def __init__(self, fault: BaseException | None) -> None:
        self._fault = fault
        self.session = _Session()

    async def __aenter__(self) -> _UnitOfWork:
        if self._fault is not None:
            raise self._fault
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Database:
    """Opens units of work, failing the first ones as told."""

    def __init__(self, *faults: BaseException) -> None:
        self._faults = list(faults)
        self.opened = 0

    def __call__(self) -> _UnitOfWork:
        self.opened += 1
        return _UnitOfWork(self._faults.pop(0) if self._faults else None)


def _hub(database: _Database) -> ChannelsHub:
    return ChannelsHub(
        uuid4(),
        uow_factory=database,  # type: ignore[arg-type]
        listeners=SignalListeners(),
        streams=LiveRegistry(),
        epoch_of=lambda _pod: asyncio.sleep(0, result=None),  # type: ignore[arg-type,return-value]
        settings=LiveSettings(
            live_authz_check_seconds=3600,
            live_idle_poll_seconds=0.001,
            live_wake_coalesce_ms=0,
        ),
        on_idle=lambda _hub: None,
        backoff=lambda _failures: 0.001,
    )


async def test_the_loop_is_started_again_after_an_unforeseen_error() -> None:
    database = _Database(RuntimeError("a bug"), KeyError("another"))
    hub = _hub(database)
    hub._started = True
    hub._start_loop()
    try:
        for _ in range(1000):
            if database.opened >= 5:
                break
            await asyncio.sleep(0.001)
        assert database.opened >= 5
        assert hub._task is not None and not hub._task.done()
        # A clean read since: the next fault backs off from the start again.
        assert hub._loop_failures == 0
    finally:
        hub._started = False
        task = hub._task
        assert task is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_the_backoff_doubles_to_its_ceiling() -> None:
    waits = [backoff(failures) for failures in range(1, 10)]
    assert waits[:3] == [1.0, 2.0, 4.0]
    assert max(waits) == 30.0 and waits == sorted(waits)


def _answering() -> Answering:
    return Answering(
        channel_id=uuid4(),
        message_id=uuid4(),
        agent_id=uuid4(),
        for_user_id=uuid4(),
        agent_run_id=str(uuid4()),
        source="test",
        key="k",
    )


async def test_the_relay_keeps_the_answer_so_far() -> None:
    answering = _answering()
    events: asyncio.Queue[JsonObject] = asyncio.Queue()
    sent: list[str] = []

    @asynccontextmanager
    async def opener(_key: str) -> AsyncIterator[AsyncIterator[JsonObject]]:
        async def stream() -> AsyncIterator[JsonObject]:
            while True:
                yield await events.get()

        yield stream()

    relays = AnswerRelays(deliver=lambda _c, _u, event: sent.append(event), longest=60)
    relays.follow(answering, opener)
    run = answering.agent_run_id
    for kind, data in (
        ("text", "Dropped"),
        ("stream_reset", ""),
        ("text", "Hello"),
        ("thinking", "hmm"),
        ("text", " there"),
    ):
        await events.put(
            {"type": "token", "kind": kind, "data": data, "agent_run_id": run}
        )
    for _ in range(100):
        if len(sent) == 5:
            break
        await asyncio.sleep(0)
    assert len(sent) == 5
    snapshot = relays.snapshot(answering)
    assert snapshot is not None
    shown = json.loads(snapshot)
    assert shown["kind"] == "snapshot" and shown["delta"] == "Hello there"
    assert relays.following(answering.id)

    await events.put(
        {
            "type": "token",
            "kind": "text",
            "data": "x" * (SNAPSHOT_CHARS * 2),
            "agent_run_id": run,
        }
    )
    for _ in range(100):
        if len(sent) == 6:
            break
        await asyncio.sleep(0)
    bounded = json.loads(relays.snapshot(answering) or "{}")
    assert len(bounded["delta"]) == SNAPSHOT_CHARS

    await events.put({"type": "completed", "agent_run_id": run})
    for _ in range(100):
        if not relays.following(answering.id):
            break
        await asyncio.sleep(0)
    assert not relays.following(answering.id)
    assert relays.snapshot(answering) is None
    await relays.close()


# -- starting once, stopping what was started ---------------------------------


class _Listener:
    def __init__(self) -> None:
        self.listening = 0

    def listen(self, _kinds: object, _on: object, _catch_up: object) -> None:
        self.listening += 1

    def forget(self, _on: object, _catch_up: object) -> None:
        # As the real one: forgetting a handler never registered is a no-op.
        self.listening = max(0, self.listening - 1)


class _Listeners:
    """A pod's signal listener that takes a few turns of the loop to open,
    as a Redis subscription does."""

    def __init__(self) -> None:
        self.listener = _Listener()

    async def of(self, _pod_id: object) -> _Listener:
        for _ in range(5):
            await asyncio.sleep(0)
        return self.listener

    async def release(self, _pod_id: object) -> None:
        return None


class _CountedHub(ChannelsHub):
    """A hub whose loop only says it ran, then waits to be stopped."""

    loops: list[asyncio.Task[None]]

    async def _run(self, *, after: float = 0.0) -> None:
        self.loops.append(asyncio.current_task())  # type: ignore[arg-type]
        await asyncio.Event().wait()


def _counted_hub(listeners: _Listeners) -> _CountedHub:
    hub = _CountedHub(
        uuid4(),
        uow_factory=_Database(),  # type: ignore[arg-type]
        listeners=listeners,  # type: ignore[arg-type]
        streams=LiveRegistry(),
        epoch_of=lambda _pod: asyncio.sleep(0, result="e1"),  # type: ignore[arg-type,return-value]
        settings=LiveSettings(),
        on_idle=lambda _hub: None,
    )
    hub.loops = []
    return hub


async def test_two_first_subscribers_start_one_loop_and_stop_ends_it() -> None:
    listeners = _Listeners()
    hub = _counted_hub(listeners)
    await asyncio.gather(*(hub._ensure_started() for _ in range(3)))
    await asyncio.sleep(0)
    assert len(hub.loops) == 1, "each first subscriber started its own loop"
    assert listeners.listener.listening == 1
    (loop,) = hub.loops
    await hub.stop()
    assert loop.cancelled()
    assert listeners.listener.listening == 0


async def test_a_hub_stopped_while_starting_starts_nothing() -> None:
    listeners = _Listeners()
    hub = _counted_hub(listeners)
    joining = asyncio.create_task(hub._ensure_started())
    await asyncio.sleep(0)
    await hub.stop()
    with pytest.raises(LiveUnavailable):
        await joining
    for _ in range(10):
        await asyncio.sleep(0)
    assert hub.loops == []
    assert listeners.listener.listening == 0


# -- access re-checks that fail are made again ---------------------------------


class _Epochs:
    def __init__(self, value: str) -> None:
        self.value = value

    async def __call__(self, _pod_id: object) -> str:
        return self.value


def _rechecking_hub(epochs: _Epochs, *, recheck_seconds: float = 3600) -> ChannelsHub:
    hub = ChannelsHub(
        uuid4(),
        uow_factory=_Database(),  # type: ignore[arg-type]
        listeners=SignalListeners(),
        streams=LiveRegistry(),
        epoch_of=epochs,  # type: ignore[arg-type]
        settings=LiveSettings(
            live_authz_check_seconds=0.001,
            live_authz_recheck_seconds=recheck_seconds,
        ),
        on_idle=lambda _hub: None,
    )
    hub.access.start(epochs.value)
    return hub


def _recheck_counter(hub: ChannelsHub, *faults: BaseException) -> list[str]:
    """Replace the hub's two re-checks; the channel one fails as told."""
    made: list[str] = []
    pending = list(faults)

    async def channels(_uow: object) -> None:
        made.append("channels")
        if pending:
            raise pending.pop(0)

    async def inbox(_uow: object, _end: object) -> None:
        made.append("inbox")

    hub._recheck = channels  # type: ignore[method-assign]
    hub.inbox.recheck = inbox  # type: ignore[method-assign]
    return made


async def test_a_recheck_that_fails_is_made_again_on_the_next_pass() -> None:
    epochs = _Epochs("e1")
    hub = _rechecking_hub(epochs)
    made = _recheck_counter(hub, OperationalError("SELECT 1", {}, Exception("gone")))
    epochs.value = "e2"
    with pytest.raises(OperationalError):
        await hub._check_access()
    # Not taken as seen: the move is still a move on the next pass.
    assert hub.access.epoch == "e1"
    await hub._check_access()
    assert made == ["channels", "channels", "inbox"]
    assert hub.access.epoch == "e2"
    await hub._check_access()
    assert made == ["channels", "channels", "inbox"], "rechecked with nothing moved"


async def test_access_is_rechecked_on_the_clock_when_no_move_is_heard() -> None:
    """A bump lost to Redis never moves the epoch: the re-check on the clock
    is what still ends a reader's access."""
    hub = _rechecking_hub(_Epochs("e1"), recheck_seconds=0.01)
    made = _recheck_counter(hub)
    await hub._check_access()
    assert made == []
    await asyncio.sleep(0.02)
    await hub._check_access()
    assert made == ["channels", "inbox"]
