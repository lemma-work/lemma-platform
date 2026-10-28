"""What modules put on the pod's live socket: topics, calls and streams.

Core owns the socket -- the connection, the loop, limits and frames -- and
knows no topic. A module contributes through ``LemmaModule.live``:

* a **topic** (``LiveTopic``) answers ``subscribe``: it checks the frame's
  parameters, decides whether the caller may watch, replays from ``since``,
  and joins the subscription to whatever feeds it. From then on it offers the
  subscription's frames to the connection; core only carries them.
* a **call** (``LiveCall``) answers ``call``: a small ephemeral action whose
  result comes back on the same socket. Anything durable is a REST request.
* a **stream** (``StreamOpener``) is a named source of transient events one
  module may relay into another's topic without importing it -- an agent's run
  tokens, relayed into the channel the run is answering.

The organization's socket carries one topic, ``org``, which modules feed
through relays and states rather than topics of their own (``org_feeds.py``).

The registry is filled once per process, when the API assembles its modules,
the way the work registry is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import JsonValue

from app.core.live.connection import LiveConnection, SocketConnection
from app.core.live.org_feeds import OrgDirectory, OrgRelay, OrgState
from app.core.live.protocol import JsonObject, SubscribeRequest


class Topic[C: SocketConnection](Protocol):
    """A kind of subscription on one kind of socket."""

    @property
    def name(self) -> str: ...

    async def subscribe(self, connection: C, request: SubscribeRequest) -> None: ...


class Call[C: SocketConnection](Protocol):
    """A method a client of one kind of socket may call."""

    @property
    def name(self) -> str: ...

    def per_minute(self) -> int: ...

    async def __call__(
        self, connection: C, params: JsonObject, *, call_id: str
    ) -> JsonValue: ...


class FrameHandlers[C: SocketConnection](Protocol):
    """The topics and calls one kind of socket answers."""

    @property
    def topics(self) -> Mapping[str, Topic[C]]: ...

    @property
    def calls(self) -> Mapping[str, Call[C]]: ...


class LiveTopic(Protocol):
    """One kind of subscription on the socket."""

    @property
    def name(self) -> str: ...

    async def subscribe(
        self, connection: LiveConnection, request: SubscribeRequest
    ) -> None:
        """Authorize, replay and join ``request`` on ``connection``.

        On success it has put a ``LiveSubscription`` at
        ``connection.subscriptions[request.id]`` and offered ``subscribed``.
        It refuses by raising ``LiveRequestInvalid`` (bad parameters) or
        ``LiveAccessDenied``; ``LiveUnavailable``, ``SQLAlchemyError`` and
        ``OSError`` say it could not decide just now. Core turns each into an
        ``error`` frame for the subscription and keeps the socket.
        """
        ...


class LiveCall(Protocol):
    """One method a client may ``call``."""

    @property
    def name(self) -> str: ...

    def per_minute(self) -> int:
        """Calls of this method one socket may make per minute."""
        ...

    async def __call__(
        self, connection: LiveConnection, params: JsonObject, *, call_id: str
    ) -> JsonValue:
        """The call's result. Refuses like a topic does."""
        ...


#: Opens a stream by key: a lease on its transient events, until it exits.
type StreamOpener = Callable[
    [str], AbstractAsyncContextManager[AsyncIterator[JsonObject]]
]


@dataclass(frozen=True, slots=True)
class LiveContribution:
    """What one module puts on the live sockets."""

    topics: Sequence[LiveTopic] = ()
    calls: Sequence[LiveCall] = ()
    streams: dict[str, StreamOpener] = field(default_factory=dict)
    #: What the module tells the ``org`` topic (``org_feeds.py``).
    org_relays: Sequence[OrgRelay] = ()
    org_states: Sequence[OrgState] = ()
    #: Which pods an organization shows a user: from the module that owns
    #: organizations, and from one module only.
    org_directory: OrgDirectory | None = None


class LiveRegistry:
    def __init__(self) -> None:
        self.topics: dict[str, LiveTopic] = {}
        self.calls: dict[str, LiveCall] = {}
        self.streams: dict[str, StreamOpener] = {}
        self.org_relays: dict[str, OrgRelay] = {}
        self.org_states: dict[str, OrgState] = {}
        self.org_directory: OrgDirectory | None = None

    def install(self, contribution: LiveContribution) -> None:
        """Add a module's topics, calls, streams and org feeds. A name two
        modules claim is a composition mistake, so it stops the process."""
        for topic in contribution.topics:
            _claim(self.topics, topic.name, topic, "topic")
        for call in contribution.calls:
            _claim(self.calls, call.name, call, "call")
        for name, opener in contribution.streams.items():
            _claim(self.streams, name, opener, "stream")
        for relay in contribution.org_relays:
            _claim(self.org_relays, relay.name, relay, "org relay")
        for state in contribution.org_states:
            _claim(self.org_states, state.name, state, "org state")
        if contribution.org_directory is not None:
            if self.org_directory is not None and _origin(
                self.org_directory
            ) != _origin(contribution.org_directory):
                raise RuntimeError("two modules answer which pods an org shows")
            self.org_directory = contribution.org_directory

    def stream(self, name: str) -> StreamOpener | None:
        return self.streams.get(name)


def _origin(value: object) -> str:
    kind = value if callable(value) and not hasattr(value, "name") else type(value)
    return f"{getattr(kind, '__module__', '')}.{getattr(kind, '__qualname__', '')}"


def _claim[T](table: dict[str, T], name: str, value: T, what: str) -> None:
    # Assembling the app again in one process (every test app does) installs
    # the same module's contribution again: that replaces it. Another module
    # claiming the name is the mistake.
    existing = table.get(name)
    if existing is not None and _origin(existing) != _origin(value):
        raise RuntimeError(f"two live {what}s are named {name!r}")
    table[name] = value


_registry = LiveRegistry()


def live_registry() -> LiveRegistry:
    return _registry


def install_live(contribution: LiveContribution) -> None:
    _registry.install(contribution)


__all__ = [
    "Call",
    "FrameHandlers",
    "LiveCall",
    "LiveContribution",
    "LiveRegistry",
    "LiveTopic",
    "StreamOpener",
    "Topic",
    "install_live",
    "live_registry",
]
