"""What modules put on the organization's live socket: the ``org`` topic's feeds.

The ``org`` topic (``org_hub.py``) tells one reader, over one subscription,
what changed in every pod of an organization they may open -- small notices a
sidebar line is redrawn from, never the detail, which the client reads over
REST. Core decides which pods a reader covers and keeps deciding it; modules
say what is worth a notice, in one of two shapes:

* an **org relay** (``OrgRelay``) tells a reader about one pod from a per-pod
  hub of its own -- channels relays a pod's ``inbox`` counts. The relay is
  handed an ``OrgReader`` per (reader, pod) and offers it events.
* an **org state** (``OrgState``) says what holds in each pod now -- the
  conversations running, the waits open, a list's version -- as items, each
  with who may hear of it. Core reads every state once for all the pods that
  may have moved, and tells each reader only what moved for them. The pod
  signals named in ``kinds`` say when to read early; a timer reads anyway, so a
  lost signal costs latency, never a notice.

Which pods of an organization a user is shown is the module that owns
organizations' to answer (``OrgDirectory``); core then holds every pod to
``pod.read``, the rule the pod's own socket is held to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import ResourceRef
from app.core.live.protocol import JsonObject


class OrgAccess(StrEnum):
    #: A member of the organization.
    MEMBER = "member"
    #: Not in it -- or no such organization: REST answers 403 for both, and
    #: so does the socket, never saying which.
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class OrgPods:
    """What an organization shows one user: whether they are in it, and the
    pods its listing shows them -- before each is held to ``pod.read``."""

    access: OrgAccess
    pod_ids: Sequence[UUID] = ()


class OrgDirectory(Protocol):
    async def pods_for(self, organization_id: UUID, user_id: UUID) -> OrgPods:
        """The organization's pods as its pod listing shows ``user_id``."""
        ...


class OrgReader(Protocol):
    """One reader of one pod, as an org relay sees them."""

    @property
    def user_id(self) -> UUID: ...

    @property
    def pod_id(self) -> UUID: ...

    @property
    def active(self) -> bool: ...

    def notify(self, events: Sequence[JsonObject]) -> None:
        """Offer events about the pod; each is sent with ``pod_id``."""
        ...

    def stale(self) -> None:
        """What the relay told this reader can no longer be trusted (their
        access in the pod moved, its feed fell behind): the client re-reads the
        pod's summary."""
        ...


class OrgWatch(Protocol):
    def release(self) -> None:
        """Stop telling the reader about the pod. Idempotent; never blocks."""
        ...


class OrgRelay(Protocol):
    @property
    def name(self) -> str: ...

    async def watch(self, reader: OrgReader) -> OrgWatch | None:
        """Start relaying the pod's notices to ``reader``; ``None`` when they
        may hear nothing of it from this relay. Raises what a topic's
        ``subscribe`` raises when it cannot decide."""
        ...


@dataclass(frozen=True, slots=True)
class Audience:
    """Who in a pod may hear of an item: every condition given must hold.

    ``users``: only these people. ``permission``: who holds it in the pod -- on
    ``resource`` when one is given (a restricted app, a granted table), else
    on the pod."""

    users: frozenset[UUID] | None = None
    permission: str | None = None
    resource: ResourceRef | None = None


@dataclass(frozen=True, slots=True)
class OrgItem:
    """Something that holds in a pod now, for an ``OrgState``.

    ``version`` says when it changed; ``held`` is sent to a reader when it
    first holds for them or its version moves, and ``ended`` when it stops --
    gone, or no longer theirs to hear of. Without ``ended`` an item never
    ends in a reader's eyes (a list's version)."""

    audience: Audience
    version: str
    held: JsonObject
    ended: JsonObject | None = None


#: An ``OrgState``'s items in one pod, by a key unique within the state.
type PodItems = Mapping[str, OrgItem]


class OrgState(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def kinds(self) -> frozenset[str]:
        """The pod signals (``signals.py``) that say it may have moved."""
        ...

    async def read(self, pod_ids: Sequence[UUID]) -> Mapping[UUID, PodItems]:
        """What holds now in each pod; a pod left out holds nothing. One read
        for every pod given, however many."""
        ...


@dataclass(frozen=True, slots=True)
class OrgFeeds:
    """What the hub reads from: the modules' relays and states, and who says
    which pods an organization shows a user."""

    directory: OrgDirectory
    relays: Sequence[OrgRelay] = ()
    states: Sequence[OrgState] = ()


__all__ = [
    "Audience",
    "OrgAccess",
    "OrgDirectory",
    "OrgFeeds",
    "OrgItem",
    "OrgPods",
    "OrgReader",
    "OrgRelay",
    "OrgState",
    "OrgWatch",
    "PodItems",
]
