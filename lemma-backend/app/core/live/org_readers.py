"""What one ``org`` subscription holds, and what each reader is told.

A subscription (``OrgSub``) covers pods; each covered pod is a ``PodReading``:
the ``OrgReader`` the modules' relays offer events to, and what each state last
told it. ``tell`` works out what moved for one reader in one state of one pod,
from what holds now and what they were last told.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.core.live.connection import OrgConnection
from app.core.live.org_feeds import Audience, OrgItem, OrgWatch, PodItems
from app.core.live.protocol import JsonObject, dumps


class Standing(Protocol):
    """Readers' standing in the organization's pods, for one turn of the hub."""

    async def opens(self, user_id: UUID, pod_id: UUID) -> bool:
        """Whether they may open the pod: ``pod.read``."""
        ...

    async def admits(self, user_id: UUID, pod_id: UUID, audience: Audience) -> bool:
        """Whether they are in ``audience`` there."""
        ...


@dataclass(frozen=True, slots=True)
class Told:
    """What a reader was last told of one item."""

    version: str
    ended: JsonObject | None


@dataclass(eq=False, slots=True)
class PodReading:
    """One subscription's view of one covered pod: an ``OrgReader`` for the
    relays, and what each state last told it."""

    sub: OrgSub
    pod_id: UUID
    watches: list[OrgWatch] = field(default_factory=list)
    told: dict[str, dict[str, Told]] = field(default_factory=dict)
    covered: bool = True

    @property
    def user_id(self) -> UUID:
        return self.sub.connection.user_id

    @property
    def active(self) -> bool:
        return self.covered and self.sub.active

    def notify(self, events: Sequence[JsonObject]) -> None:
        if self.active and events:
            self.sub.offer(self.pod_id, events)

    def stale(self) -> None:
        self.notify([{"type": "pod.stale"}])

    def end(self) -> None:
        self.covered = False
        watches, self.watches = self.watches, []
        for watch in watches:
            watch.release()


@dataclass(eq=False, slots=True)
class OrgSub:
    id: str
    connection: OrgConnection
    active: bool = True
    pods: dict[UUID, PodReading] = field(default_factory=dict)
    on_release: Callable[[OrgSub], None] | None = field(default=None, repr=False)

    @property
    def user_id(self) -> UUID:
        return self.connection.user_id

    def offer(self, pod_id: UUID | None, events: Sequence[JsonObject]) -> None:
        stamped = [
            dumps(event if pod_id is None else {**event, "pod_id": str(pod_id)})
            for event in events
        ]
        self.connection.offer_events(self.id, None, stamped)

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        for reading in self.pods.values():
            reading.end()
        if self.on_release is not None:
            self.on_release(self)


def fingerprint(items: PodItems) -> int:
    """What a reader could be told of ``items``: each item's key, version,
    audience and whether it can end."""
    return hash(
        frozenset(
            (key, item.version, item.audience, item.ended is None)
            for key, item in items.items()
        )
    )


async def visible(
    access: Standing, reading: PodReading, items: PodItems
) -> dict[str, OrgItem]:
    return {
        key: item
        for key, item in items.items()
        if await access.admits(reading.user_id, reading.pod_id, item.audience)
    }


def told_of(items: Mapping[str, OrgItem]) -> dict[str, Told]:
    """What a reader has been told once told ``items``."""
    return {key: Told(item.version, item.ended) for key, item in items.items()}


@dataclass(frozen=True, slots=True)
class Tell:
    """What to tell one reader of one state in one pod, and what they will
    then have been told."""

    events: list[JsonObject]
    told: dict[str, Told]


async def tell(
    access: Standing, reading: PodReading, state: str, items: PodItems
) -> Tell:
    """What moved for one reader in one state of one pod since they were last
    told: what holds for them now and did not, or holds in a new version, and
    what held and no longer does."""
    shown = await visible(access, reading, items)
    before = reading.told.get(state, {})
    events: list[JsonObject] = []
    for key, item in shown.items():
        was = before.get(key)
        if was is None or was.version != item.version:
            events.append(item.held)
    for key, was in before.items():
        if key not in shown and was.ended is not None:
            events.append(was.ended)
    return Tell(events=events, told=told_of(shown))


__all__ = [
    "OrgSub",
    "PodReading",
    "Standing",
    "Tell",
    "Told",
    "fingerprint",
    "tell",
    "told_of",
    "visible",
]
