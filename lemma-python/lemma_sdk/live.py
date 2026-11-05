"""Watch record changes on a pod's live socket (``/pods/{pod_id}/live``).

Any other topic the socket carries -- ``channel`` (one channel's timeline),
``inbox`` (the reader's counts) -- is watched with :func:`watch_topic`; its
events arrive as :class:`LiveTopicEvent`, each the server's event as sent.

One websocket per pod carries any number of subscriptions. ``LiveSession`` is
the protocol with no I/O -- the frames to send, and what each frame received
means -- so the CLI and :func:`watch_changes` share it. :func:`watch_changes`
adds the socket: it connects, subscribes, reconnects with backoff, and resumes
each subscription from its own last cursor. A subscription the server could
not start just now (an ``error`` with code ``unavailable``) is sent again after
its own backoff, on the same socket; any other refusal ends it.

    async for event in pod.watch_changes("tickets"):
        if isinstance(event, LiveChange):
            print(event.op, event.record_id, event.row)
        elif isinstance(event, LiveResync):
            refetch()            # the server could not replay what was missed

``watch_changes`` needs the ``websockets`` package: ``pip install
"lemma-sdk[live]"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import ssl
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

LiveOp = Literal["insert", "update", "delete"]
_OPS: dict[object, LiveOp] = {
    "insert": "insert",
    "update": "update",
    "delete": "delete",
}

#: The server accepts the handshake, then closes with this when the session is
#: missing, invalid or expired: refresh the token and reconnect.
CLOSE_UNAUTHENTICATED = 4401
#: Closes no retry can fix.
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404

_RECONNECT_BASE_DELAY_SECONDS = 0.5
_RECONNECT_MAX_DELAY_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class LiveReady:
    """A subscription is live; changes after ``cursor`` will follow."""

    subscription: str
    cursor: str


@dataclass(frozen=True, slots=True)
class LiveChange:
    """One record's change. Several changes to a record close together arrive
    as one, at its latest state; ``row`` is ``None`` for a delete."""

    subscription: str
    op: LiveOp
    table: str
    record_id: str
    row: dict[str, object] | None
    #: Pass back as ``since`` to resume after this change.
    cursor: str


@dataclass(frozen=True, slots=True)
class LiveResync:
    """The server could not replay what was missed. Refetch, then carry on:
    changes after ``cursor`` keep coming."""

    subscription: str
    cursor: str
    reason: str


@dataclass(frozen=True, slots=True)
class LiveEnded:
    """The server refused or ended a subscription; it will not resume."""

    subscription: str
    reason: str


@dataclass(frozen=True, slots=True)
class LiveTopicEvent:
    """One event of a topic other than ``records`` -- ``line.created``,
    ``inbox.updated``, ``typing``, ... -- as the server sent it."""

    subscription: str
    type: str
    data: dict[str, object]
    #: Pass back as ``since`` to resume after this event; ``""`` for a topic
    #: with no history (``inbox``).
    cursor: str


LiveEvent = LiveReady | LiveChange | LiveResync | LiveEnded | LiveTopicEvent


class LiveAuthError(Exception):
    """The server rejected the session (close 4401)."""


class LiveAccessError(Exception):
    """No access to the pod, or no such pod (close 4403 / 4404)."""


def live_ws_url(base_url: str, pod_id: str) -> str:
    root = base_url.rstrip("/")
    if root.startswith("https://"):
        root = "wss://" + root.removeprefix("https://")
    elif root.startswith("http://"):
        root = "ws://" + root.removeprefix("http://")
    elif not root.startswith(("ws://", "wss://")):
        # No scheme (bare host): assume TLS, like a browser.
        root = "wss://" + root.removeprefix("//")
    return f"{root}/pods/{pod_id}/live"


@dataclass(slots=True)
class _Subscription:
    id: str
    table: str | None
    ops: tuple[LiveOp, ...] | None
    cursor: str | None
    when: Mapping[str, object] | None = None
    topic: str = "records"
    params: Mapping[str, object] | None = None


@dataclass(slots=True)
class LiveSession:
    """The client half of the protocol for one socket, without the socket.

    Keeps each subscription's cursor across reconnects: after every ``ready``,
    send :meth:`subscribe_frames` and each subscription resumes where it was.
    A subscription refused as ``unavailable`` is kept, and handed back by
    :meth:`take_retries` to be sent again after its backoff -- at most
    ``max_retries`` times in a row, when set.
    """

    _subscriptions: dict[str, _Subscription] = field(default_factory=dict)
    _next: int = 1
    max_retries: int | None = None
    #: Consecutive ``unavailable`` refusals per subscription since it was last
    #: ``subscribed``.
    _attempts: dict[str, int] = field(default_factory=dict)
    _due: list[tuple[str, float]] = field(default_factory=list)

    def add(
        self,
        *,
        table: str | None = None,
        ops: Sequence[LiveOp] | None = None,
        since: str | None = None,
        when: Mapping[str, object] | None = None,
        topic: str = "records",
        params: Mapping[str, object] | None = None,
    ) -> str:
        """Add a subscription; ``when`` filters by the row's current values.

        ``when`` uses the trigger grammar's value operators (``equals``,
        ``not_equals``, ``in``, ``not_in``, or a bare value for equality) and
        needs ``table``. Deletes are always delivered. Another ``topic`` takes
        its own ``params`` (``{"channel_id": ...}`` for ``channel``).
        """
        subscription_id = f"s{self._next}"
        self._next += 1
        self._subscriptions[subscription_id] = _Subscription(
            subscription_id,
            table,
            tuple(ops) if ops else None,
            since,
            when,
            topic,
            dict(params) if params else None,
        )
        return subscription_id

    @property
    def active(self) -> bool:
        return bool(self._subscriptions)

    def cursor(self, subscription_id: str) -> str | None:
        subscription = self._subscriptions.get(subscription_id)
        return subscription.cursor if subscription else None

    def subscribe_frames(self) -> list[str]:
        # A reconnect sends every subscription anyway: retries due on the old
        # socket are moot.
        self._due.clear()
        return [_subscribe_frame(s) for s in self._subscriptions.values()]

    def take_retries(self) -> list[tuple[str, float]]:
        """Subscribe frames to send again, each with the seconds to wait."""
        due, self._due = self._due, []
        return [
            (_subscribe_frame(self._subscriptions[subscription_id]), delay)
            for subscription_id, delay in due
            if subscription_id in self._subscriptions
        ]

    def handle(self, raw: str | bytes) -> tuple[bool, list[LiveEvent]]:
        """Read one server frame: (is it ``ready``, the events it carries)."""
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError, TypeError, ValueError:
            return False, []
        if not isinstance(frame, dict):
            return False, []
        kind = frame.get("type")
        if kind == "ready":
            return True, []
        subscription = self._subscriptions.get(str(frame.get("id")))
        if subscription is None:
            return False, []
        cursor = frame.get("cursor") or frame.get("stream_id")
        if (
            isinstance(cursor, str)
            and cursor
            and kind in ("subscribed", "changes", "events", "resync")
        ):
            subscription.cursor = cursor
        current = subscription.cursor or ""
        if kind == "subscribed":
            self._attempts.pop(subscription.id, None)
            return False, [LiveReady(subscription.id, current)]
        if kind == "changes":
            changes = frame.get("changes")
            events: list[LiveEvent] = []
            for change in changes if isinstance(changes, list) else []:
                op = _OPS.get(change.get("op")) if isinstance(change, dict) else None
                if op is None:
                    continue
                row = change.get("row")
                events.append(
                    LiveChange(
                        subscription=subscription.id,
                        op=op,
                        table=str(change.get("table") or ""),
                        record_id=str(change.get("record_id")),
                        row=row if isinstance(row, dict) else None,
                        cursor=current,
                    )
                )
            return False, events
        if kind == "events":
            found = frame.get("events")
            return False, [
                LiveTopicEvent(
                    subscription=subscription.id,
                    type=str(event.get("type")),
                    data=event,
                    cursor=current,
                )
                for event in (found if isinstance(found, list) else [])
                if isinstance(event, dict)
            ]
        if kind == "resync":
            return False, [
                LiveResync(subscription.id, current, str(frame.get("reason")))
            ]
        if kind == "error":
            if frame.get("code") == "unavailable":
                attempt = self._attempts.get(subscription.id, 0)
                if self.max_retries is None or attempt < self.max_retries:
                    self._attempts[subscription.id] = attempt + 1
                    self._due.append((subscription.id, reconnect_delay(attempt)))
                    return False, []
                self._subscriptions.pop(subscription.id, None)
                return False, [
                    LiveEnded(
                        subscription.id, "max retry attempts reached (unavailable)"
                    )
                ]
            self._subscriptions.pop(subscription.id, None)
            return False, [LiveEnded(subscription.id, f"refused: {frame.get('code')}")]
        if kind == "unsubscribed" and frame.get("reason") != "client":
            self._subscriptions.pop(subscription.id, None)
            return False, [LiveEnded(subscription.id, str(frame.get("reason")))]
        return False, []


def _subscribe_frame(subscription: _Subscription) -> str:
    frame: dict[str, object] = {
        **(subscription.params or {}),
        "op": "subscribe",
        "id": subscription.id,
        "topic": subscription.topic,
    }
    if subscription.table:
        frame["table"] = subscription.table
    if subscription.ops:
        frame["ops"] = list(subscription.ops)
    if subscription.cursor:
        frame["since"] = subscription.cursor
    if subscription.when:
        frame["when"] = dict(subscription.when)
    return json.dumps(frame)


def reconnect_delay(attempt: int) -> float:
    """Full-jitter exponential backoff, capped."""
    ceiling = min(
        _RECONNECT_MAX_DELAY_SECONDS,
        _RECONNECT_BASE_DELAY_SECONDS * (2 ** max(0, attempt)),
    )
    return random.uniform(0.0, ceiling)


async def watch_changes(
    *,
    base_url: str,
    pod_id: str,
    token: Callable[[], str],
    refresh: Callable[[], bool] | None = None,
    table: str | None = None,
    ops: Sequence[LiveOp] | None = None,
    since: str | None = None,
    when: Mapping[str, object] | None = None,
    verify_ssl: bool = True,
    max_reconnects: int | None = None,
) -> AsyncIterator[LiveEvent]:
    """Yield the subscription's events until it ends or the caller stops.

    ``token`` is asked for the access token on every connect; on a 4401 close
    ``refresh`` (when given) gets one chance to replace it. Raises
    ``LiveAuthError`` / ``LiveAccessError`` for closes no reconnect can fix.
    """
    session = LiveSession(max_retries=max_reconnects)
    session.add(table=table, ops=ops, since=since, when=when)
    async for event in _watch(
        session,
        base_url=base_url,
        pod_id=pod_id,
        token=token,
        refresh=refresh,
        verify_ssl=verify_ssl,
        max_reconnects=max_reconnects,
    ):
        yield event


async def watch_topic(
    topic: str,
    *,
    base_url: str,
    pod_id: str,
    token: Callable[[], str],
    refresh: Callable[[], bool] | None = None,
    params: Mapping[str, object] | None = None,
    since: str | None = None,
    verify_ssl: bool = True,
    max_reconnects: int | None = None,
) -> AsyncIterator[LiveEvent]:
    """Yield one subscription to ``topic`` -- ``channel`` with
    ``params={"channel_id": ...}``, or ``inbox`` -- its events as
    ``LiveTopicEvent``, resuming from its own cursor across reconnects as
    :func:`watch_changes` does. A ``LiveResync``: refetch over REST."""
    session = LiveSession(max_retries=max_reconnects)
    session.add(topic=topic, params=params, since=since)
    async for event in _watch(
        session,
        base_url=base_url,
        pod_id=pod_id,
        token=token,
        refresh=refresh,
        verify_ssl=verify_ssl,
        max_reconnects=max_reconnects,
    ):
        yield event


async def _watch(
    session: LiveSession,
    *,
    base_url: str,
    pod_id: str,
    token: Callable[[], str],
    refresh: Callable[[], bool] | None,
    verify_ssl: bool,
    max_reconnects: int | None,
) -> AsyncIterator[LiveEvent]:
    try:
        import websockets
        from websockets.exceptions import ConnectionClosed, WebSocketException
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise ImportError(
            'Watching changes needs the websockets package: pip install "lemma-sdk[live]"'
        ) from exc

    url = live_ws_url(base_url, pod_id)
    ssl_option: ssl.SSLContext | None = None
    if url.startswith("wss://") and not verify_ssl:
        ssl_option = ssl.create_default_context()
        ssl_option.check_hostname = False
        ssl_option.verify_mode = ssl.CERT_NONE
    attempt = 0
    refreshed = False
    while session.active:
        close_code: int | None = None
        retries: set[asyncio.Task[None]] = set()
        try:
            async with websockets.connect(
                url,
                additional_headers={"Authorization": f"Bearer {token()}"},
                ssl=ssl_option,
            ) as websocket:

                async def resend(frame: str, delay: float) -> None:
                    await asyncio.sleep(delay)
                    with contextlib.suppress(ConnectionClosed):
                        await websocket.send(frame)

                async for raw in websocket:
                    ready, events = session.handle(raw)
                    if ready:
                        attempt, refreshed = 0, False
                        for task in list(retries):
                            task.cancel()
                        retries.clear()
                        for frame in session.subscribe_frames():
                            await websocket.send(frame)
                    for frame, delay in session.take_retries():
                        task = asyncio.create_task(resend(frame, delay))
                        retries.add(task)
                        task.add_done_callback(retries.discard)
                    for event in events:
                        yield event
                    if not session.active:
                        return
        except ConnectionClosed as exc:
            close_code = exc.rcvd.code if exc.rcvd is not None else None
        except OSError, WebSocketException:
            close_code = None
        finally:
            for task in list(retries):
                task.cancel()
        if close_code in (CLOSE_FORBIDDEN, CLOSE_NOT_FOUND):
            raise LiveAccessError("No access to this pod's changes, or no such pod.")
        if close_code == CLOSE_UNAUTHENTICATED:
            if refreshed or refresh is None or not refresh():
                raise LiveAuthError("The session was rejected; sign in again.")
            refreshed = True
            continue
        if max_reconnects is not None and attempt >= max_reconnects:
            raise ConnectionError("Live socket: max reconnect attempts reached.")
        await asyncio.sleep(reconnect_delay(attempt))
        attempt += 1


__all__ = [
    "LiveAccessError",
    "LiveAuthError",
    "LiveChange",
    "LiveEnded",
    "LiveEvent",
    "LiveReady",
    "LiveResync",
    "LiveSession",
    "LiveTopicEvent",
    "live_ws_url",
    "reconnect_delay",
    "watch_changes",
    "watch_topic",
]
