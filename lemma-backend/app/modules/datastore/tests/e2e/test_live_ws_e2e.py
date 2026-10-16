"""The pod live socket over a real websocket, against the real change log.

A uvicorn server runs the app; clients are ``websockets`` connections over TCP,
so back-pressure, close codes and frame order are the ones a browser sees.
Writes go through the record API and reach the hub by the change log and its
Redis wake, never by a test shortcut.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import socket
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.modules.datastore.changes.gate import reset_change_log_gate
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.config_live import live_settings
from app.modules.datastore.domain.live import SubscribeRequest
from app.modules.datastore.services.live.connection import LiveConnection
from app.modules.datastore.services.live.registry import LiveHubRegistry
from app.modules.datastore.services.live.access import TableLiveAccess
from app.modules.datastore.services.live.feed import ChangeLogFeed
from app.modules.datastore.tests.e2e.harness import DatastoreApi, signup_user
from app.modules.test_support.e2e.runtime import backend_server as _backend_server
from app.core.authorization.authz_epoch import pod_authz_epoch
from app.core.infrastructure.channels.channel_service import get_channel_service

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server

_TABLES = ("tickets", "comments", "orders")


@pytest.fixture(autouse=True)
def _change_log_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(datastore_settings, "datastore_change_log_mode", "on")
    reset_change_log_gate()


@pytest_asyncio.fixture
async def live_pod(pod_api: DatastoreApi) -> DatastoreApi:
    for name in _TABLES:
        await pod_api.create_table(
            {
                "name": name,
                "enable_rls": False,
                "columns": [{"name": "body", "type": "TEXT", "required": False}],
            }
        )
    return pod_api


class Live:
    """One client socket: send ops, read frames, keep what was read."""

    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.frames: list[dict] = []

    async def send(self, **frame: object) -> None:
        await self.ws.send(json.dumps(frame))

    async def recv(self, timeout: float = 15) -> dict:
        frame = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
        self.frames.append(frame)
        return frame

    async def until(
        self, kind: str, sub: str | None = None, timeout: float = 20
    ) -> dict:
        async def _next() -> dict:
            while True:
                frame = await self.recv(timeout)
                if frame["type"] == kind and (sub is None or frame.get("id") == sub):
                    return frame

        return await asyncio.wait_for(_next(), timeout)

    async def quiet(self, seconds: float = 1.5) -> list[dict]:
        """Frames that arrive within ``seconds``; normally none."""
        got: list[dict] = []
        try:
            async with asyncio.timeout(seconds):
                while True:
                    got.append(await self.recv(seconds))
        except TimeoutError:
            return got

    async def subscribe(
        self, sub: str, table: str | None = None, **extra: object
    ) -> dict:
        frame: dict[str, object] = {"op": "subscribe", "id": sub, "topic": "records"}
        if table is not None:
            frame["table"] = table
        frame.update(extra)
        await self.send(**frame)
        return await self.until("subscribed", sub)

    def record_ids(self, sub: str) -> list[str]:
        return [
            change["record_id"]
            for frame in self.frames
            if frame["type"] == "changes" and frame["id"] == sub
            for change in frame["changes"]
        ]


def _ws_url(server: dict[str, str], path: str) -> str:
    return server["host_base_url"].replace("http://", "ws://") + path


async def _open(server: dict[str, str], pod_id: str, token: str, **kwargs) -> Live:
    ws = await connect(
        _ws_url(server, f"/pods/{pod_id}/live"),
        additional_headers={"Authorization": f"Bearer {token}"},
        **kwargs,
    )
    live = Live(ws)
    ready = await live.recv()
    assert ready["type"] == "ready", ready
    assert ready["connection"]
    return live


@pytest_asyncio.fixture
async def sockets() -> AsyncIterator[list[Live]]:
    opened: list[Live] = []
    yield opened
    for live in opened:
        await live.ws.close()


async def test_one_socket_three_subscriptions_import_resyncs_only_its_table(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets
) -> None:
    live = await _open(backend_server, live_pod.pod_id, fixed_test_user["token"])
    sockets.append(live)
    for sub, table in zip(("t", "c", "o"), _TABLES):
        await live.subscribe(sub, table)

    # Ten thousand rows in the largest batches the API takes, all at once.
    imported = asyncio.gather(
        *(
            live_pod.bulk_create(
                "tickets", [{"body": f"row {batch}-{n}"} for n in range(1_000)]
            )
            for batch in range(10)
        )
    )
    comment = await live_pod.create_record("comments", {"body": "during the import"})
    await imported
    order = await live_pod.create_record("orders", {"body": "after the import"})

    resync = await live.until("resync", "t", timeout=60)
    assert resync["reason"] == "too_many"
    assert resync["cursor"] and resync["stream_id"] == resync["cursor"]
    await live.until("changes", "o", timeout=30)
    if comment["id"] not in live.record_ids("c"):
        await live.until("changes", "c")
    assert live.record_ids("c") == [comment["id"]]
    assert live.record_ids("o") == [order["id"]]
    # The import never reached the client row by row.
    assert len(live.record_ids("t")) <= live_settings.live_max_changes_per_batch


async def test_rls_rows_reach_only_their_owner(
    pod_api: DatastoreApi,
    member_users,
    async_client,
    fixed_test_user,
    backend_server,
    sockets,
) -> None:
    await pod_api.create_table(
        {
            "name": "private_notes",
            "enable_rls": True,
            "columns": [{"name": "body", "type": "TEXT", "required": True}],
        }
    )
    editor = member_users["editor"]
    editor_api = DatastoreApi(async_client, pod_api.pod_id, user=editor)
    owner = await _open(backend_server, pod_api.pod_id, fixed_test_user["token"])
    other = await _open(backend_server, pod_api.pod_id, editor["token"])
    sockets.extend([owner, other])
    await owner.subscribe("p", "private_notes")
    await other.subscribe("p", "private_notes")

    editors = await editor_api.create_record("private_notes", {"body": "editor secret"})
    owners = await pod_api.create_record("private_notes", {"body": "owner row"})

    await other.until("changes", "p")
    await owner.until("changes", "p")
    await owner.quiet(1.0)
    await other.quiet(1.0)
    assert owner.record_ids("p") == [owners["id"]]
    assert other.record_ids("p") == [editors["id"]]
    [row] = [
        c["row"] for f in owner.frames if f["type"] == "changes" for c in f["changes"]
    ]
    assert row["body"] == "owner row"


async def test_removed_member_is_unsubscribed(
    live_pod: DatastoreApi, member_users, authenticated_client, backend_server, sockets
) -> None:
    editor = member_users["editor"]
    live = await _open(backend_server, live_pod.pod_id, editor["token"])
    sockets.append(live)
    await live.subscribe("t", "tickets")

    members = await authenticated_client.get(f"/pods/{live_pod.pod_id}/members")
    assert members.status_code == 200, members.text
    [member] = [
        item for item in members.json()["items"] if str(item["user_id"]) == editor["id"]
    ]
    removed = await authenticated_client.delete(
        f"/pods/{live_pod.pod_id}/members/{member['pod_member_id']}"
    )
    assert removed.status_code == 204, removed.text

    ended = await live.until("unsubscribed", "t", timeout=20)
    assert ended["reason"] == "access_revoked"
    await live_pod.create_record("tickets", {"body": "after removal"})
    assert [f for f in await live.quiet(1.5) if f["type"] == "changes"] == []


async def test_reconnect_with_since_is_gap_free(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets
) -> None:
    token = fixed_test_user["token"]
    first = await _open(backend_server, live_pod.pod_id, token)
    await first.subscribe("t", "tickets")
    seen = await live_pod.create_record("tickets", {"body": "seen"})
    frame = await first.until("changes", "t")
    assert [c["record_id"] for c in frame["changes"]] == [seen["id"]]
    await first.ws.close()

    missed = [
        (await live_pod.create_record("tickets", {"body": f"missed {n}"}))["id"]
        for n in range(3)
    ]
    again = await _open(backend_server, live_pod.pod_id, token)
    sockets.append(again)
    await again.subscribe("t", "tickets", since=frame["cursor"])
    after = await live_pod.create_record("tickets", {"body": "after"})

    async def _all_arrived() -> None:
        while after["id"] not in again.record_ids("t"):
            await again.until("changes", "t")

    await asyncio.wait_for(_all_arrived(), 20)
    assert again.record_ids("t") == [*missed, after["id"]]


async def test_expired_or_foreign_since_answers_resync(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets
) -> None:
    live = await _open(backend_server, live_pod.pod_id, fixed_test_user["token"])
    sockets.append(live)
    subscribed = await live.subscribe("t", "tickets", since="1697040000000-0")
    resync = await live.until("resync", "t")
    assert resync["reason"] == "cursor_expired"
    assert resync["cursor"] == subscribed["cursor"]
    record = await live_pod.create_record("tickets", {"body": "continues"})
    await live.until("changes", "t")
    assert live.record_ids("t") == [record["id"]]


async def test_when_sends_only_rows_that_match_now(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets
) -> None:
    live = await _open(backend_server, live_pod.pod_id, fixed_test_user["token"])
    sockets.append(live)
    await live.subscribe("w", "tickets", when={"body": {"in": ["a"]}})
    await live.subscribe("all", "tickets")
    gone = await live_pod.create_record("tickets", {"body": "a"})
    moved = await live_pod.create_record("tickets", {"body": "b"})
    never = await live_pod.create_record("tickets", {"body": "c"})
    await live_pod.update_record("tickets", moved["id"], {"body": "a"})
    await live_pod.delete_record("tickets", gone["id"])
    last = await live_pod.create_record("tickets", {"body": "a"})

    async def _seen(sub: str, record_id: str) -> None:
        while record_id not in live.record_ids(sub):
            await live.recv()

    await asyncio.wait_for(_seen("w", last["id"]), 20)
    await asyncio.wait_for(_seen("all", never["id"]), 20)
    sent = [
        change
        for frame in live.frames
        if frame["type"] == "changes" and frame["id"] == "w"
        for change in frame["changes"]
    ]
    assert never["id"] not in {change["record_id"] for change in sent}
    assert all(c["row"]["body"] == "a" for c in sent if c["op"] != "delete")
    assert moved["id"] in {change["record_id"] for change in sent}
    # The deleted row cannot be tested any more; a client holding it must hear.
    assert [c["op"] for c in sent if c["record_id"] == gone["id"]][-1] == "delete"


async def test_refused_subscription_keeps_the_socket(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets
) -> None:
    live = await _open(backend_server, live_pod.pod_id, fixed_test_user["token"])
    sockets.append(live)
    await live.send(op="subscribe", id="x", table="no_such_table")
    assert (await live.until("error", "x"))["code"] == "not_found"
    await live.send(op="subscribe", id="w", table="tickets", when={"body": {"eq": "a"}})
    assert (await live.until("error", "w"))["code"] == "invalid"
    # A live subscription sees the current row only: nothing to ask "changed" of.
    await live.send(
        op="subscribe", id="m", table="tickets", when={"body": {"changed": True}}
    )
    moved = await live.until("error", "m")
    assert (moved["code"], "changed" in moved["message"]) == ("invalid", True)
    await live.send(op="ping")
    assert (await live.until("pong"))["type"] == "pong"
    await live.subscribe("t", "tickets")
    await live.send(op="unsubscribe", id="t")
    assert (await live.until("unsubscribed", "t"))["reason"] == "client"


async def test_unauthenticated_and_forbidden_close_after_accept(
    live_pod: DatastoreApi, member_users, backend_server
) -> None:
    async with connect(_ws_url(backend_server, f"/pods/{live_pod.pod_id}/live")) as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), 10)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401

    stranger = "00000000-0000-4000-8000-000000000000"
    async with connect(
        _ws_url(backend_server, f"/pods/{stranger}/live"),
        additional_headers={
            "Authorization": f"Bearer {member_users['viewer']['token']}"
        },
    ) as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), 10)
    assert closed.value.rcvd is not None and closed.value.rcvd.code in (4403, 4404)


async def _refused(
    server: dict[str, str], pod_id: str, token: str | None
) -> tuple[int, list[str]]:
    """Open a socket that must be refused: its close code and any frames sent."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    frames: list[str] = []
    async with connect(
        _ws_url(server, f"/pods/{pod_id}/live"), additional_headers=headers
    ) as ws:
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                frames.append(str(await asyncio.wait_for(ws.recv(), 10)))
    assert closed.value.rcvd is not None, "closed without a close frame"
    return closed.value.rcvd.code, frames


async def test_invalid_token_is_closed_4401_without_ready(
    live_pod: DatastoreApi, backend_server
) -> None:
    for token in (None, "not-a-session-token"):
        code, frames = await _refused(backend_server, live_pod.pod_id, token)
        assert (code, frames) == (4401, []), token


async def test_non_member_is_refused_and_hears_no_change(
    live_pod: DatastoreApi, async_client, backend_server
) -> None:
    """A signed-in user outside a real pod gets no ``ready`` and no row."""
    outsider = await signup_user(async_client, "live-outsider")
    headers = {"Authorization": f"Bearer {outsider['token']}"}
    async with connect(
        _ws_url(backend_server, f"/pods/{live_pod.pod_id}/live"),
        additional_headers=headers,
    ) as ws:
        # Write before reading anything: a frame for it must never be queued.
        await live_pod.create_record("tickets", {"body": "members only"})
        frames: list[str] = []
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                frames.append(str(await asyncio.wait_for(ws.recv(), 10)))
    assert frames == []
    assert closed.value.rcvd is not None and closed.value.rcvd.code in (4403, 4404)


async def _restricted_table(pod: DatastoreApi, name: str) -> None:
    await pod.create_table(
        {
            "name": name,
            "enable_rls": False,
            "visibility": "RESTRICTED",
            "columns": [{"name": "body", "type": "TEXT", "required": False}],
        }
    )


async def test_restricted_table_is_refused_and_never_streamed(
    live_pod: DatastoreApi, member_users, backend_server, sockets
) -> None:
    """A member with no grant on a RESTRICTED table hears none of its rows."""
    await _restricted_table(live_pod, "vault")
    live = await _open(backend_server, live_pod.pod_id, member_users["viewer"]["token"])
    sockets.append(live)
    await live.send(op="subscribe", id="v", topic="records", table="vault")
    assert (await live.until("error", "v"))["code"] in ("forbidden", "not_found")
    # Every table the viewer can read: the restricted one is not among them.
    await live.subscribe("all")

    await live_pod.create_record("vault", {"body": "restricted secret"})
    visible = await live_pod.create_record("tickets", {"body": "open row"})
    await live.until("changes", "all")
    await live.quiet(1.5)
    assert live.record_ids("v") == []
    assert live.record_ids("all") == [visible["id"]]
    assert "restricted secret" not in json.dumps(live.frames)


async def test_socket_limit_per_user_per_pod(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets, monkeypatch
) -> None:
    monkeypatch.setattr(live_settings, "live_max_sockets_per_user_per_pod", 2)
    token = fixed_test_user["token"]
    sockets.append(await _open(backend_server, live_pod.pod_id, token))
    sockets.append(await _open(backend_server, live_pod.pod_id, token))
    async with connect(
        _ws_url(backend_server, f"/pods/{live_pod.pod_id}/live"),
        additional_headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), 10)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == 4429

    # Closing one frees its slot.
    await sockets.pop().ws.close()
    sockets.append(await _open(backend_server, live_pod.pod_id, token))


class _StalledClient:
    """A live socket that subscribes and then stops reading, for real.

    A ``websockets`` client is no use here: it keeps reading the transport into
    its own buffers whatever ``max_queue`` says, so the server's sends never
    block and whether its queue overflows comes down to timing. This one speaks
    just enough of the protocol by hand (RFC 6455: an upgrade, then masked
    client frames) and then leaves the kernel's small buffers to fill.
    """

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.received = bytearray()

    @classmethod
    async def open(
        cls, server: dict[str, str], pod_id: str, token: str
    ) -> _StalledClient:
        host, port = server["host_base_url"].removeprefix("http://").split(":")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.setblocking(False)
        await asyncio.get_running_loop().sock_connect(sock, (host, int(port)))
        client = cls(sock)
        key = base64.b64encode(os.urandom(16)).decode()
        await client._send(
            (
                f"GET /pods/{pod_id}/live HTTP/1.1\r\nHost: {host}:{port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                f"Authorization: Bearer {token}\r\n\r\n"
            ).encode()
        )
        await client.read_until(b"\r\n\r\n")
        assert client.received.startswith(b"HTTP/1.1 101"), bytes(client.received)
        await client.read_until(b'"type":"ready"')
        return client

    async def _send(self, data: bytes) -> None:
        await asyncio.get_running_loop().sock_sendall(self.sock, data)

    async def send_json(self, frame: dict[str, object]) -> None:
        payload = json.dumps(frame).encode()
        assert len(payload) < 126
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        await self._send(bytes([0x81, 0x80 | len(payload)]) + mask + masked)

    async def read_until(self, marker: bytes, timeout: float = 15) -> None:
        await self.read_match(re.escape(marker), timeout=timeout)

    async def read_match(self, pattern: bytes, timeout: float = 15) -> re.Match[bytes]:
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(timeout):
            while (found := re.search(pattern, self.received)) is None:
                chunk = await loop.sock_recv(self.sock, 1 << 20)
                if not chunk:
                    raise ConnectionError(f"closed before {pattern!r}")
                self.received += chunk
        return found

    def close(self) -> None:
        self.sock.close()


async def test_slow_consumer_is_resynced_without_slowing_the_hub(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets, monkeypatch
) -> None:
    monkeypatch.setattr(live_settings, "live_socket_queue_bytes", 64 * 1024)
    token = fixed_test_user["token"]
    slow = await _StalledClient.open(backend_server, live_pod.pod_id, token)
    try:
        await slow.send_json(
            {"op": "subscribe", "id": "t", "topic": "records", "table": "tickets"}
        )
        await slow.read_until(b'"type":"subscribed"')
        fast = await _open(backend_server, live_pod.pod_id, token)
        sockets.append(fast)
        await fast.subscribe("t", "tickets")

        written: list[str] = []
        # Far more than the kernel buffers between the two ends hold (a few
        # MB), so the server's sends to the slow client block and its queue,
        # capped at 64 KiB above, overflows.
        padding = "x" * 8000
        for batch in range(12):
            await live_pod.bulk_create(
                "tickets", [{"body": padding} for _ in range(400)]
            )
            # A batch this size arrives in several frames.
            while len(written) < (batch + 1) * 400:
                frame = await fast.until("changes", "t", timeout=20)
                written.extend(c["record_id"] for c in frame["changes"])

        # The fast client kept receiving every batch while the slow one stalled.
        assert len(written) == 12 * 400
        # Reading the slow client now: behind what the kernel buffered, its
        # queue was cut back to a resync.
        slow.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        resync = await slow.read_match(rb'\{"type":"resync",[^}]*\}', timeout=30)
        frame = json.loads(resync.group())
        assert frame["id"] == "t" and frame["reason"] == "overflow", frame
    finally:
        slow.close()


async def test_old_endpoint_still_streams_through_the_hub(
    live_pod: DatastoreApi, fixed_test_user, backend_server
) -> None:
    url = _ws_url(
        backend_server, f"/pods/{live_pod.pod_id}/datastore/changes?table=tickets"
    )
    async with connect(
        url, additional_headers={"Authorization": f"Bearer {fixed_test_user['token']}"}
    ) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert ready["type"] == "ready" and ready["since"]
        record = await live_pod.create_record("tickets", {"body": "via the shim"})
        await live_pod.create_record("orders", {"body": "other table"})
        frame = json.loads(await asyncio.wait_for(ws.recv(), 15))
        assert frame["type"] == "datastore.record.insert"
        assert frame["operation"] == "INSERT"
        assert frame["record_id"] == record["id"]
        assert frame["payload"]["body"] == "via the shim"
        assert frame["stream_id"]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 1.5)


async def test_two_hubs_on_one_pod_both_deliver(
    live_pod: DatastoreApi, fixed_test_user, backend_server, sockets
) -> None:
    """A second API process is a second hub with its own cursor and wake.

    The server under test holds one hub; a second registry here is what a
    second process holds. Both read the same log and both deliver.
    """
    live = await _open(backend_server, live_pod.pod_id, fixed_test_user["token"])
    sockets.append(live)
    await live.subscribe("t", "tickets")

    other_process = LiveHubRegistry(
        feed_factory=ChangeLogFeed,
        access_factory=TableLiveAccess,
        channel_factory=get_channel_service,
        epoch_of=pod_authz_epoch,
        settings=live_settings,
    )
    received: asyncio.Queue[dict] = asyncio.Queue()

    async def collect(text: str) -> None:
        received.put_nowait(json.loads(text))

    connection = LiveConnection(
        pod_id=UUID(live_pod.pod_id),
        user_id=UUID(fixed_test_user["id"]),
        send_text=collect,
        queue_bytes=1 << 20,
    )
    hub = await other_process.hub(UUID(live_pod.pod_id))
    hub.attach(connection)
    sender = asyncio.create_task(connection.run_sender())
    try:
        await hub.subscribe(
            connection,
            SubscribeRequest(op="subscribe", id="t", table="tickets"),
            max_subscriptions=5,
        )
        assert (await asyncio.wait_for(received.get(), 10))["type"] == "subscribed"
        record = await live_pod.create_record("tickets", {"body": "both"})
        theirs = await asyncio.wait_for(received.get(), 15)
        ours = await live.until("changes", "t")
        assert [c["record_id"] for c in theirs["changes"]] == [record["id"]]
        assert [c["record_id"] for c in ours["changes"]] == [record["id"]]
    finally:
        connection.close()
        hub.detach(connection)
        sender.cancel()
        await other_process.close()


async def test_table_read_without_record_read_streams_no_rows(
    live_pod: DatastoreApi,
    member_users,
    authenticated_client,
    async_client,
    backend_server,
    sockets,
) -> None:
    """Seeing a table's schema is not reading its rows.

    The record API refuses a role holding ``datastore.table.read`` alone on a
    RESTRICTED table; the live socket must not hand it those rows either.
    """
    await _restricted_table(live_pod, "vault")
    granted = await authenticated_client.put(
        f"/pods/{live_pod.pod_id}/roles/POD_VIEWER/permissions",
        json={
            "grants": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": "vault",
                    "permission_ids": ["datastore.table.read"],
                }
            ]
        },
    )
    assert granted.status_code == 200, granted.text
    viewer = member_users["viewer"]
    rows = await DatastoreApi(async_client, live_pod.pod_id, user=viewer).request(
        "GET", f"/pods/{live_pod.pod_id}/datastore/tables/vault/records"
    )
    assert rows.status_code == 403, rows.text

    live = await _open(backend_server, live_pod.pod_id, viewer["token"])
    sockets.append(live)
    await live.send(op="subscribe", id="v", topic="records", table="vault")
    await live.subscribe("all")
    await live_pod.create_record("vault", {"body": "restricted secret"})
    visible = await live_pod.create_record("tickets", {"body": "open row"})
    while visible["id"] not in live.record_ids("all"):
        await live.until("changes", "all")
    await live.quiet(1.5)
    assert live.record_ids("v") == []
    assert live.record_ids("all") == [visible["id"]]
    assert "restricted secret" not in json.dumps(live.frames)
