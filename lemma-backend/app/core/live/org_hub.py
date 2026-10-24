"""The ``org`` topic: one reader's notices from every pod of an organization.

A client showing every pod at once -- unread counts, what is running, what
waits on the reader -- holds one subscription here instead of a socket per
pod. It is told small change notices, each naming its pod; the detail is read
over REST.

**Which pods.** A subscription covers the pods the organization's listing
shows the reader (``OrgDirectory``) that they may also open -- ``pod.read``,
the rule the pod's own socket is held to -- up to ``LIVE_ORG_MAX_PODS`` of
them. Right after ``subscribed`` comes ``pods`` naming them. Which pods it
covers is decided again whenever a covered pod's grants epoch moves, whenever
the organization's channel says its pods or members moved, and every
``LIVE_AUTHZ_RECHECK_SECONDS`` regardless; a pod gained is ``pod.added``, a
pod lost ``pod.removed``, and nothing more is said of it. A reader no longer
in the organization is sent ``unsubscribed`` (``access_revoked``) and the
socket closes ``4403``.

**What is said.** Modules decide (``org_feeds.py``). A relay is handed a
reader per covered pod and offers it events -- channels relays the pod's
``inbox``. A state is read once for every pod that may have moved and each
reader is told only what moved for them: an item that now holds for them, or
whose version moved, is its ``held`` event; one that stopped holding is its
``ended`` event. Each reader starts from what held when they subscribed (or
when a pod was added), which the REST summary they read next also shows.

**One hub per organization per process**, shared by every subscription to
it there, as a pod's hubs are. It listens to each covered pod's signals for
the states' kinds and reads a state early for the pods signalled; it reads
every state for every covered pod each ``LIVE_ORG_POLL_SECONDS`` anyway, so a
signal lost costs latency, never a notice. Database work grows with covered
pods and the states that moved, not with readers: one read per state per
batch, and a reader's access in a pod is a cached role snapshot
(``org_standing.py``). What a subscription holds, and how what a reader is
told is worked out, is ``org_readers.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.live.config import LiveSettings
from app.core.live.connection import OrgConnection
from app.core.live.org_feeds import OrgAccess, OrgFeeds, PodItems
from app.core.live.org_readers import (
    OrgSub,
    PodReading,
    Tell,
    fingerprint,
    tell,
    told_of,
    visible,
)
from app.core.live.org_standing import StandingFactory
from app.core.live.protocol import (
    CLOSE_FORBIDDEN,
    LiveAccessDenied,
    LiveErrorCode,
    LiveUnavailable,
    UnsubscribedReason,
)
from app.core.live.signals import CatchUp, Signal, SignalHandler, SignalListeners
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

logger = get_logger(__name__)

#: Reads several pods' grants epochs at once.
EpochsReader = Callable[[Sequence[UUID]], Awaitable[Mapping[UUID, str | None]]]

#: The organization's own signal: its pods or their members moved.
PODS_SIGNAL = "pods"


# -- the hub ------------------------------------------------------------------------


class OrgHub:
    def __init__(
        self,
        organization_id: UUID,
        *,
        feeds: OrgFeeds,
        standing: StandingFactory,
        listeners: SignalListeners,
        epochs_of: EpochsReader,
        settings: LiveSettings,
        on_idle: Callable[[OrgHub], None],
    ) -> None:
        self.organization_id = organization_id
        self._feeds = feeds
        self._standing = standing
        self._listeners = listeners
        self._epochs_of = epochs_of
        self._settings = settings
        self._on_idle = on_idle
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._subs: set[OrgSub] = set()
        #: Every covered pod's readings: who is told of it.
        self._readers: dict[UUID, set[PodReading]] = {}
        #: The signal handlers registered on each covered pod's listener.
        self._listening: dict[UUID, tuple[SignalHandler, CatchUp]] = {}
        #: Per state, the pods to read again.
        self._dirty: dict[str, set[UUID]] = {}
        #: Pods whose grants moved: who covers them is decided again.
        self._moved: set[UUID] = set()
        #: Pods whose readers' standing moved since their states were last
        #: weighed: weighed again for every reader, whatever was read.
        self._reweigh: set[UUID] = set()
        #: What each state held in each pod when its readers were last told;
        #: a read finding the same tells nobody anything, so costs no reader.
        self._held: dict[tuple[str, UUID], int] = {}
        #: Decide again which pods every subscription covers.
        self._relist = False
        self._epochs: dict[UUID, str | None] = {}
        self._joining = 0
        self._started = False
        self._stopped = False
        self._starting: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._next_poll = 0.0
        self._next_check = 0.0
        self._next_recheck = 0.0
        self._last_read = 0.0
        #: Unforeseen ends of the loop since it last turned cleanly.
        self._loop_failures = 0
        self.idle_since: float | None = None

    # -- lifecycle ------------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self._stopped:
            raise LiveUnavailable("the organization's feed is stopping")
        if self._started:
            if self._task is None or self._task.done():
                self._start_loop()
            return
        if self._starting is None or self._starting.done():
            self._starting = create_background_task(
                self._start(), name=f"live-org-start-{self.organization_id}"
            )
        await asyncio.shield(self._starting)
        if not self._started:
            raise LiveUnavailable("the organization's feed is stopping")

    async def _start(self) -> None:
        listener = await self._listeners.of_org(self.organization_id)
        if self._stopped or self._started:
            return
        listener.listen([PODS_SIGNAL], self._on_org_signal, self._on_org_catch_up)
        now = time.monotonic()
        self._next_poll = now + self._settings.live_org_poll_seconds
        self._next_check = now + self._settings.live_authz_check_seconds
        self._next_recheck = now + self._settings.live_authz_recheck_seconds
        self._started = True
        self._start_loop()

    def _start_loop(self, *, after: float = 0.0) -> None:
        self._task = create_background_task(
            self._run(after=after), name=f"live-org-{self.organization_id}"
        )
        self._task.add_done_callback(self._loop_ended)

    def _loop_ended(self, task: asyncio.Task[None]) -> None:
        """The loop met an error nobody foresaw -- a bug, not an outage. It is
        started again after a backoff rather than leave every reader of the
        organization in this process silent."""
        if task.cancelled() or not self._started or task is not self._task:
            return
        fault = task.exception()
        if fault is None:
            return
        self._loop_failures += 1
        logger.error(
            "live.org.hub_loop_failed",
            organization_id=str(self.organization_id),
            error_type=type(fault).__name__,
            failures=self._loop_failures,
            exc_info=fault,
        )
        self._start_loop(after=_backoff(self._loop_failures))

    async def stop(self) -> None:
        self._stopped = True
        self._started = False
        starting, self._starting = self._starting, None
        if starting is not None and not starting.done():
            starting.cancel()
            await asyncio.wait({starting})
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for sub in list(self._subs):
            sub.release()
        for pod_id in list(self._listening):
            await self._stop_listening(pod_id)
        org_listener = await self._listeners.of_org(self.organization_id)
        org_listener.forget(self._on_org_signal, self._on_org_catch_up)
        await self._listeners.release_org(self.organization_id)

    def subscription_count(self) -> int:
        return len(self._subs)

    def covered_pods(self) -> set[UUID]:
        return set(self._readers)

    def _maybe_idle(self) -> None:
        if not self._subs and not self._joining and self.idle_since is None:
            self.idle_since = time.monotonic()
            self._on_idle(self)

    # -- joining ----------------------------------------------------------------

    async def join(self, connection: OrgConnection, subscription_id: str) -> OrgSub:
        """Cover the pods the reader may open, from what holds in them now;
        then ``subscribed`` and ``pods``."""
        self._joining += 1
        self.idle_since = None
        try:
            await self._ensure_started()
            async with self._lock:
                access, covered, more = await self._pods_for(connection.user_id)
                if access is not OrgAccess.MEMBER:
                    raise LiveAccessDenied(
                        LiveErrorCode.NOT_FOUND, "Organization not found"
                    )
                sub = OrgSub(
                    id=subscription_id,
                    connection=connection,
                    on_release=self._release,
                )
                connection.subscriptions[subscription_id] = sub
                self._subs.add(sub)
                joined = False
                try:
                    await self._cover(sub, covered)
                    joined = True
                finally:
                    if not joined:
                        sub.release()
                connection.offer_subscribed(subscription_id, None)
                sub.offer(
                    None,
                    [
                        {
                            "type": "pods",
                            "pod_ids": [str(pod_id) for pod_id in covered],
                            "more": more,
                        }
                    ],
                )
                return sub
        finally:
            self._joining -= 1
            self._maybe_idle()

    def _release(self, sub: OrgSub) -> None:
        self._subs.discard(sub)
        for pod_id, reading in list(sub.pods.items()):
            self._drop_reading(pod_id, reading)
        sub.pods.clear()
        self._maybe_idle()

    async def _pods_for(self, user_id: UUID) -> tuple[OrgAccess, list[UUID], bool]:
        """Whether ``user_id`` is in the organization; the pods a subscription
        of theirs covers, and whether more were left out past the cap."""
        listing = await self._feeds.directory.pods_for(self.organization_id, user_id)
        if listing.access is not OrgAccess.MEMBER:
            return listing.access, [], False
        cap = self._settings.live_org_max_pods
        covered: list[UUID] = []
        more = False
        async with self._standing() as access:
            for pod_id in dict.fromkeys(listing.pod_ids):
                if not await access.opens(user_id, pod_id):
                    continue
                if len(covered) == cap:
                    more = True
                    break
                covered.append(pod_id)
        return listing.access, covered, more

    async def _cover(self, sub: OrgSub, pod_ids: Sequence[UUID]) -> None:
        """Start telling ``sub`` of ``pod_ids``: its relays watching, and what
        each state holds there now taken as what it was told."""
        if not pod_ids:
            return
        held = await self._read(pod_ids, {state.name for state in self._feeds.states})
        unheard = [pod_id for pod_id in pod_ids if pod_id not in self._listening]
        epochs = await self._epochs_of(unheard) if unheard else {}
        for state in self._feeds.states:
            for pod_id in pod_ids:
                # Its next read is weighed for every reader: what this one was
                # told it held is newer than what the others were.
                self._held.pop((state.name, pod_id), None)
        async with self._standing() as access:
            for pod_id in pod_ids:
                reading = PodReading(sub=sub, pod_id=pod_id)
                for state in self._feeds.states:
                    shown = await visible(
                        access, reading, held.get(state.name, {}).get(pod_id, {})
                    )
                    reading.told[state.name] = told_of(shown)
                sub.pods[pod_id] = reading
                self._readers.setdefault(pod_id, set()).add(reading)
                await self._listen(pod_id, epochs.get(pod_id))
                for relay in self._feeds.relays:
                    watch = await relay.watch(reading)
                    if watch is not None:
                        reading.watches.append(watch)

    def _drop_reading(self, pod_id: UUID, reading: PodReading) -> None:
        reading.end()
        readings = self._readers.get(pod_id)
        if readings is None:
            return
        readings.discard(reading)
        if not readings:
            self._readers.pop(pod_id, None)
            self._epochs.pop(pod_id, None)
            self._reweigh.discard(pod_id)
            for pods in self._dirty.values():
                pods.discard(pod_id)
            for state in self._feeds.states:
                self._held.pop((state.name, pod_id), None)
            listening = self._listening.pop(pod_id, None)
            if listening is not None:
                create_background_task(
                    self._forget(pod_id, *listening), name="live-org-forget"
                )

    # -- signals ----------------------------------------------------------------

    def _kinds(self) -> dict[str, list[str]]:
        """Per pod signal kind, the states it may have moved."""
        kinds: dict[str, list[str]] = {}
        for state in self._feeds.states:
            for kind in state.kinds:
                kinds.setdefault(kind, []).append(state.name)
        return kinds

    async def _listen(self, pod_id: UUID, epoch: str | None) -> None:
        if pod_id in self._listening:
            return
        kinds = self._kinds()
        if not kinds:
            return
        names = [state.name for state in self._feeds.states]

        def heard(signal: Signal) -> None:
            for name in kinds.get(str(signal.get("k")), ()):
                self._dirty.setdefault(name, set()).add(pod_id)
            self._wake.set()

        def catch_up() -> None:
            for name in names:
                self._dirty.setdefault(name, set()).add(pod_id)
            self._wake.set()

        self._listening[pod_id] = (heard, catch_up)
        self._epochs.setdefault(pod_id, epoch)
        listener = await self._listeners.of(pod_id)
        listener.listen(list(kinds), heard, catch_up)

    async def _forget(
        self, pod_id: UUID, handler: SignalHandler, catch_up: CatchUp
    ) -> None:
        listener = await self._listeners.of(pod_id)
        listener.forget(handler, catch_up)
        await self._listeners.release(pod_id)

    async def _stop_listening(self, pod_id: UUID) -> None:
        listening = self._listening.pop(pod_id, None)
        if listening is not None:
            await self._forget(pod_id, *listening)

    def _on_org_signal(self, signal: Signal) -> None:
        if signal.get("k") == PODS_SIGNAL:
            self._relist = True
            self._wake.set()

    def _on_org_catch_up(self) -> None:
        self._relist = True
        self._wake.set()

    # -- the loop ---------------------------------------------------------------

    async def _run(self, *, after: float = 0.0) -> None:
        if after:
            await asyncio.sleep(after)
            self._everything()
        failures = 0
        while True:
            polled = await self._wait()
            try:
                await self._turn(polled=polled)
            except (
                SQLAlchemyError,
                RedisError,
                OSError,
                TimeoutError,
                LiveUnavailable,
            ) as exc:
                failures += 1
                logger.error(
                    "live.org.hub_turn_failed",
                    organization_id=str(self.organization_id),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                self._everything()
                await asyncio.sleep(_backoff(failures))
            else:
                failures = self._loop_failures = 0

    def _everything(self) -> None:
        """What was missed is found by deciding and reading it all again."""
        for state in self._feeds.states:
            self._dirty.setdefault(state.name, set()).update(self._readers)
        self._relist = True

    async def _wait(self) -> bool:
        """Sleep until there is something to do; whether a poll is due."""
        timeout = max(
            0.0,
            min(self._next_poll, self._next_check, self._next_recheck)
            - time.monotonic(),
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        gap = self._last_read + self._settings.live_wake_coalesce_ms / 1000
        if gap > time.monotonic():
            await asyncio.sleep(gap - time.monotonic())
        self._wake.clear()
        self._last_read = time.monotonic()
        if self._last_read >= self._next_poll:
            self._next_poll = self._last_read + self._settings.live_org_poll_seconds
            return True
        return False

    async def _turn(self, *, polled: bool) -> None:
        now = time.monotonic()
        if now >= self._next_check:
            self._next_check = now + self._settings.live_authz_check_seconds
            await self._check_epochs()
        if now >= self._next_recheck:
            self._next_recheck = now + self._settings.live_authz_recheck_seconds
            self._relist = True
        if polled:
            for state in self._feeds.states:
                self._dirty.setdefault(state.name, set()).update(self._readers)
        async with self._lock:
            await self._decide_pods()
            await self._drain()

    async def _check_epochs(self) -> None:
        pods = sorted(self._readers)
        if not pods:
            return
        epochs = await self._epochs_of(pods)
        for pod_id in pods:
            epoch = epochs.get(pod_id)
            if epoch is not None and epoch != self._epochs.get(pod_id):
                self._moved.add(pod_id)

    async def _decide_pods(self) -> None:
        """Decide again which pods each subscription covers, where something
        says it may have moved. Epochs are taken as seen only once decided."""
        relist, self._relist = self._relist, False
        moved, self._moved = self._moved, set()
        if not relist and not moved:
            return
        decided = False
        try:
            epochs = await self._epochs_of(sorted(moved)) if moved else {}
            for sub in list(self._subs):
                if sub.active and (relist or moved & set(sub.pods)):
                    await self._recover(sub)
            # A reader's standing moved: what each state holds for them is
            # weighed again on this turn.
            weigh = moved & set(self._readers)
            self._reweigh |= weigh
            for state in self._feeds.states:
                self._dirty.setdefault(state.name, set()).update(weigh)
            for pod_id, epoch in epochs.items():
                if pod_id in self._readers:
                    self._epochs[pod_id] = epoch
            decided = True
        finally:
            if not decided:
                self._relist = self._relist or relist
                self._moved |= moved

    async def _recover(self, sub: OrgSub) -> None:
        access, covered, more = await self._pods_for(sub.user_id)
        if access is not OrgAccess.MEMBER:
            # Out of the organization: the socket goes, with the code a
            # connect would now be refused with.
            sub.connection.offer_unsubscribed(
                sub.id, UnsubscribedReason.ACCESS_REVOKED.value
            )
            sub.connection.close_with(
                CLOSE_FORBIDDEN, "You are no longer in this organization"
            )
            return
        gone = [pod_id for pod_id in sub.pods if pod_id not in covered]
        added = [pod_id for pod_id in covered if pod_id not in sub.pods]
        for pod_id in gone:
            self._drop_reading(pod_id, sub.pods.pop(pod_id))
        if gone:
            sub.offer(None, [{"type": "pod.removed", "pod_id": str(p)} for p in gone])
        if added:
            await self._cover(sub, added)
            sub.offer(
                None,
                [{"type": "pod.added", "pod_id": str(p), "more": more} for p in added],
            )

    async def _read(
        self, pod_ids: Sequence[UUID], names: set[str]
    ) -> dict[str, Mapping[UUID, PodItems]]:
        found: dict[str, Mapping[UUID, PodItems]] = {}
        for state in self._feeds.states:
            if state.name in names and pod_ids:
                found[state.name] = await state.read(sorted(pod_ids))
        return found

    async def _drain(self) -> None:
        """Read each state for the pods it may have moved in, and tell each
        reader what moved for them. A reader is told before what it was told
        is moved, so a read that fails part way is made again whole."""
        dirty, self._dirty = self._dirty, {}
        dirty = {
            name: pods & set(self._readers) for name, pods in dirty.items() if pods
        }
        if not any(dirty.values()):
            return
        reweigh, self._reweigh = self._reweigh, set()
        held_now: dict[tuple[str, UUID], int] = {}
        batches: list[tuple[PodReading, str, Tell]] = []
        read = False
        try:
            async with self._standing() as access:
                for state in self._feeds.states:
                    pods = dirty.get(state.name)
                    if not pods:
                        continue
                    held = await state.read(sorted(pods))
                    for pod_id in sorted(pods):
                        items = held.get(pod_id, {})
                        key = (state.name, pod_id)
                        held_now[key] = fingerprint(items)
                        if (
                            pod_id not in reweigh
                            and self._held.get(key) == held_now[key]
                        ):
                            continue
                        for reading in list(self._readers.get(pod_id, ())):
                            if not reading.active:
                                continue
                            told = await tell(access, reading, state.name, items)
                            batches.append((reading, state.name, told))
            read = True
        finally:
            if not read:
                for name, pods in dirty.items():
                    self._dirty.setdefault(name, set()).update(pods)
                self._reweigh |= reweigh
        # Every reader worked out: only now is what each was told moved.
        for key, held_as in held_now.items():
            if key[1] in self._readers:
                self._held[key] = held_as
        for reading, state, told in batches:
            reading.told[state] = told.told
            reading.notify(told.events)


def _backoff(failures: int) -> float:
    """1 s doubling to 30 s."""
    return min(30.0, float(2 ** max(0, min(failures - 1, 5))))


__all__ = ["EpochsReader", "OrgHub", "PODS_SIGNAL"]
