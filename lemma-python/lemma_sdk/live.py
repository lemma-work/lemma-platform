"""Watch record changes on a pod's live socket (``/pods/{pod_id}/live``).

One websocket per pod carries any number of subscriptions. ``LiveSession`` is
the protocol with no I/O -- the frames to send, and what each frame received
means -- so the CLI and :func:`watch_changes` share it. :func:`watch_changes`
adds the socket: it connects, subscribes, reconnects with backoff, and resumes
each subscription from its own last cursor.

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


LiveEvent = LiveReady | LiveChange | LiveResync | LiveEnded


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


@dataclass(slots=True)
class LiveSession:
    """The client half of the protocol for one socket, without the socket.

    Keeps each subscription's cursor across reconnects: after every ``ready``,
    send :meth:`subscribe_frames` and each subscription resumes where it was.
    """

    _subscriptions: dict[str, _Subscription] = field(default_factory=dict)
    _next: int = 1

    def add(
        self,
        *,
        table: str | None = None,
        ops: Sequence[LiveOp] | None = None,
        since: str | None = None,
        when: Mapping[str, object] | None = None,
    ) -> str:
        """Add a subscription; ``when`` filters by the row's current values.

        ``when`` uses the trigger grammar's value operators (``equals``,
        ``not_equals``, ``in``, ``not_in``, or a bare value for equality) and
        needs ``table``. Deletes are always delivered.
        """
        subscription_id = f"s{self._next}"
        self._next += 1
        self._subscriptions[subscription_id] = _Subscription(
            subscription_id, table, tuple(ops) if ops else None, since, when
        )
        return subscription_id

    @property
    def active(self) -> bool:
        return bool(self._subscriptions)

    def cursor(self, subscription_id: str) -> str | None:
        subscription = self._subscriptions.get(subscription_id)
        return subscription.cursor if subscription else None

    def subscribe_frames(self) -> list[str]:
        frames: list[str] = []
        for subscription in self._subscriptions.values():
            frame: dict[str, object] = {
                "op": "subscribe",
                "id": subscription.id,
                "topic": "records",
            }
            if subscription.table:
                frame["table"] = subscription.table
            if subscription.ops:
                frame["ops"] = list(subscription.ops)
            if subscription.cursor:
                frame["since"] = subscription.cursor
            if subscription.when:
                frame["when"] = dict(subscription.when)
            frames.append(json.dumps(frame))
        return frames

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
            and kind in ("subscribed", "changes", "resync")
        ):
            subscription.cursor = cursor
        current = subscription.cursor or ""
        if kind == "subscribed":
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
        if kind == "resync":
            return False, [
                LiveResync(subscription.id, current, str(frame.get("reason")))
            ]
        if kind == "error":
            self._subscriptions.pop(subscription.id, None)
            return False, [LiveEnded(subscription.id, f"refused: {frame.get('code')}")]
        if kind == "unsubscribed" and frame.get("reason") != "client":
            self._subscriptions.pop(subscription.id, None)
            return False, [LiveEnded(subscription.id, str(frame.get("reason")))]
        return False, []


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
    try:
        import websockets
        from websockets.exceptions import ConnectionClosed, WebSocketException
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise ImportError(
            'Watching changes needs the websockets package: pip install "lemma-sdk[live]"'
        ) from exc

    session = LiveSession()
    session.add(table=table, ops=ops, since=since, when=when)
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
        try:
            async with websockets.connect(
                url,
                additional_headers={"Authorization": f"Bearer {token()}"},
                ssl=ssl_option,
            ) as websocket:
                async for raw in websocket:
                    ready, events = session.handle(raw)
                    if ready:
                        attempt, refreshed = 0, False
                        for frame in session.subscribe_frames():
                            await websocket.send(frame)
                    for event in events:
                        yield event
                    if not session.active:
                        return
        except ConnectionClosed as exc:
            close_code = exc.rcvd.code if exc.rcvd is not None else None
        except OSError, WebSocketException:
            close_code = None
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
    "live_ws_url",
    "reconnect_delay",
    "watch_changes",
]
