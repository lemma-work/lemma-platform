"""E2E tests for the datastore changes websocket.

Drives ``/pods/{pod_id}/datastore/changes`` over a raw ASGI websocket
(``ApplicationCommunicator``) and asserts the live record-change stream, table
filtering, RLS per-user row scoping, and auth rejection.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse

import pytest
import pytest_asyncio
from asgiref.testing import ApplicationCommunicator

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("datastore_outbox_dispatcher")]


def _ws_communicator(
    app,
    pod_id: str,
    token: str,
    *,
    table: str | None = None,
    since: str | None = None,
) -> ApplicationCommunicator:
    params: dict[str, str] = {}
    if table is not None:
        params["table"] = table
    if since is not None:
        params["since"] = since
    query = urllib.parse.urlencode(params).encode() if params else b""
    path = f"/pods/{pod_id}/datastore/changes"
    headers = [(b"host", b"testserver")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return ApplicationCommunicator(
        app,
        {
            "type": "websocket",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": headers,
            "scheme": "ws",
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
        },
    )


async def _expect_accept_and_ready(communicator: ApplicationCommunicator) -> dict:
    accepted = await communicator.receive_output(timeout=5)
    assert accepted["type"] == "websocket.accept", accepted
    ready = await _recv_json(communicator, timeout=5)
    assert ready["type"] == "ready", ready
    assert "since" in ready
    return ready


async def _recv_json(
    communicator: ApplicationCommunicator, timeout: float = 10
) -> dict:
    message = await communicator.receive_output(timeout=timeout)
    assert message["type"] == "websocket.send", message
    return json.loads(message["text"])


async def _assert_no_frame(
    communicator: ApplicationCommunicator, timeout: float = 3
) -> None:
    # receive_nothing polls the output queue without cancelling the app task,
    # unlike receive_output which kills the websocket on timeout.
    assert await communicator.receive_nothing(timeout=timeout, interval=0.05)


@pytest_asyncio.fixture
async def notes_pod(pod_api: DatastoreApi) -> DatastoreApi:
    await pod_api.create_table(
        {
            "name": "notes",
            "enable_rls": False,
            "columns": [{"name": "body", "type": "TEXT", "required": True}],
        }
    )
    return pod_api


async def test_changes_ws_streams_record_lifecycle(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    created = await notes_pod.create_record("notes", {"body": "hello"})
    insert = await _recv_json(communicator)
    assert insert["type"] == "datastore.record.insert"
    assert insert["table_name"] == "notes"
    assert insert["operation"] == "INSERT"
    assert insert["payload"]["body"] == "hello"
    assert insert["stream_id"]
    record_id = created["id"]

    await notes_pod.update_record("notes", record_id, {"body": "hello world"})
    update = await _recv_json(communicator)
    assert update["type"] == "datastore.record.update"
    assert update["payload"]["body"] == "hello world"

    await notes_pod.delete_record("notes", record_id)
    delete = await _recv_json(communicator)
    assert delete["type"] == "datastore.record.delete"
    assert delete["record_id"] == record_id

    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait(timeout=3)


async def test_changes_ws_table_filter_excludes_other_tables(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    await notes_pod.create_table(
        {
            "name": "tasks",
            "enable_rls": False,
            "columns": [{"name": "title", "type": "TEXT", "required": True}],
        }
    )
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"], table="notes"
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    # A change to a table outside the filter must not be delivered.
    await notes_pod.create_record("tasks", {"title": "ignored"})
    await _assert_no_frame(communicator)

    # A change to the filtered table is delivered.
    await notes_pod.create_record("notes", {"body": "kept"})
    frame = await _recv_json(communicator)
    assert frame["table_name"] == "notes"
    assert frame["payload"]["body"] == "kept"

    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait(timeout=3)


async def test_changes_ws_rls_scopes_rows_to_owner(
    pod_api: DatastoreApi,
    member_users,
    async_client,
    fixed_test_user,
    test_app,
):
    await pod_api.create_table(
        {
            "name": "private_notes",
            "enable_rls": True,
            "columns": [{"name": "body", "type": "TEXT", "required": True}],
        }
    )
    editor = member_users["editor"]
    editor_api = DatastoreApi(async_client, pod_api.pod_id, user=editor)

    owner_ws = _ws_communicator(
        test_app, pod_api.pod_id, fixed_test_user["token"], table="private_notes"
    )
    await owner_ws.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(owner_ws)

    # The editor writes a row they own; the pod owner's stream must not see it,
    # because RLS scopes each row to its owner.
    await editor_api.create_record("private_notes", {"body": "editor secret"})
    await _assert_no_frame(owner_ws)

    # The editor's own stream does receive their row.
    editor_ws = _ws_communicator(
        test_app, pod_api.pod_id, editor["token"], table="private_notes"
    )
    await editor_ws.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(editor_ws)
    await editor_api.create_record("private_notes", {"body": "editor only"})
    frame = await _recv_json(editor_ws)
    assert frame["payload"]["body"] == "editor only"

    # The owner's own row IS delivered on their stream.
    await pod_api.create_record("private_notes", {"body": "owner row"})
    owner_frame = await _recv_json(owner_ws)
    assert owner_frame["payload"]["body"] == "owner row"

    await owner_ws.send_input({"type": "websocket.disconnect", "code": 1000})
    await editor_ws.send_input({"type": "websocket.disconnect", "code": 1000})
    await owner_ws.wait(timeout=3)
    await editor_ws.wait(timeout=3)


async def test_changes_ws_rejects_unauthenticated(
    notes_pod: DatastoreApi,
    test_app,
):
    communicator = _ws_communicator(test_app, notes_pod.pod_id, token="")
    await communicator.send_input({"type": "websocket.connect"})
    closed = await communicator.receive_output(timeout=5)
    assert closed["type"] == "websocket.close"


async def test_changes_ws_disconnect_immediately_after_connect(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """Client that disconnects immediately after the handshake must not crash the server.

    Regression guard for the uvicorn accept-race: in production, a client that
    closes the TCP connection between the HTTP upgrade and the server's
    websocket.accept() causes uvicorn to raise RuntimeError.  The ASGI layer
    should always complete cleanly regardless of when the client goes away.
    """
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    # Connect, then immediately signal disconnect before consuming any output.
    await communicator.send_input({"type": "websocket.connect"})
    await communicator.send_input({"type": "websocket.disconnect", "code": 1001})

    # Drain whatever the server sent (accept + ready frame) before it noticed
    # the disconnect.  We don't assert on the exact sequence — both "accepted
    # then closed" and "rejected with close" are valid outcomes.  What matters
    # is that the application task finishes without raising.
    for _ in range(5):
        try:
            msg = await communicator.receive_output(timeout=2)
            if msg["type"] == "websocket.close":
                break
        except Exception:
            break

    # The ASGI app task must have exited cleanly (no unhandled exception).
    await communicator.wait(timeout=3)


async def test_changes_ws_disconnect_during_streaming(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """Client that disconnects mid-stream must not leave a runaway server task.

    Once the server has accepted and started streaming, a sudden client
    disconnect should be detected by _wait_for_disconnect and cause both the
    forwarder task and the disconnect-watcher to be cancelled and awaited.
    """
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    # Disconnect mid-stream without sending a change event.
    await communicator.send_input({"type": "websocket.disconnect", "code": 1001})

    # Server should finish within a short window.
    await communicator.wait(timeout=3)


async def test_changes_ws_stream_resumption_replays_missed_events(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """Reconnecting with ?since=<stream_id> replays only the events that arrived
    after that cursor, not earlier ones.

    Simulates the canonical reconnect pattern: client tracks the last stream_id
    it received, disconnects (e.g. network blip), and reconnects to pick up any
    changes that happened while it was offline without re-receiving old events.
    """
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    # Create two records and collect their frames while connected.
    r1 = await notes_pod.create_record("notes", {"body": "record-1"})
    r2 = await notes_pod.create_record("notes", {"body": "record-2"})
    frame1 = await _recv_json(communicator)
    frame2 = await _recv_json(communicator)
    assert frame1["record_id"] == r1["id"]
    assert frame2["record_id"] == r2["id"]
    # Save the stream cursor after record-2.
    cursor_after_r2 = frame2["stream_id"]

    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait(timeout=3)

    # Create more records while the client is offline.
    r3 = await notes_pod.create_record("notes", {"body": "record-3"})
    r4 = await notes_pod.create_record("notes", {"body": "record-4"})

    # Reconnect from the cursor after record-2.
    reconnected = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"], since=cursor_after_r2
    )
    await reconnected.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(reconnected)

    # Should receive record-3 and record-4 — not record-1 or record-2.
    missed_ids = set()
    for _ in range(2):
        frame = await _recv_json(reconnected)
        assert frame["type"] == "datastore.record.insert"
        missed_ids.add(frame["record_id"])
    assert missed_ids == {r3["id"], r4["id"]}

    # No additional frames for this pod.
    await _assert_no_frame(reconnected)

    await reconnected.send_input({"type": "websocket.disconnect", "code": 1000})
    await reconnected.wait(timeout=3)


async def test_changes_ws_concurrent_subscribers_all_receive_events(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """Multiple simultaneous WebSocket connections to the same pod all receive
    the same record-change events.

    Verifies that each connection has an independent subscriber and that the
    fan-out from the Redis stream reaches every connected client.
    """
    token = fixed_test_user["token"]
    c1 = _ws_communicator(test_app, notes_pod.pod_id, token)
    c2 = _ws_communicator(test_app, notes_pod.pod_id, token)
    c3 = _ws_communicator(test_app, notes_pod.pod_id, token)

    for c in (c1, c2, c3):
        await c.send_input({"type": "websocket.connect"})
        await _expect_accept_and_ready(c)

    record = await notes_pod.create_record("notes", {"body": "broadcast"})
    record_id = record["id"]

    # All three connections must receive the same insert frame.
    frames = await asyncio.gather(_recv_json(c1), _recv_json(c2), _recv_json(c3))
    for frame in frames:
        assert frame["type"] == "datastore.record.insert"
        assert frame["record_id"] == record_id
        assert frame["payload"]["body"] == "broadcast"

    for c in (c1, c2, c3):
        await c.send_input({"type": "websocket.disconnect", "code": 1000})
    for c in (c1, c2, c3):
        await c.wait(timeout=3)


async def test_changes_ws_no_events_missed_under_rapid_writes(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """Rapid concurrent writes do not cause events to be dropped by the stream.

    Creates 10 records in parallel and asserts the subscriber eventually
    receives an insert frame for every one of them. Event ordering across
    concurrent writes is non-deterministic; completeness is what matters.
    """
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    records = await asyncio.gather(
        *[notes_pod.create_record("notes", {"body": f"item-{i}"}) for i in range(10)]
    )
    expected_ids = {r["id"] for r in records}

    received_ids: set[str] = set()
    for _ in range(10):
        frame = await _recv_json(communicator, timeout=15)
        assert frame["type"] == "datastore.record.insert"
        received_ids.add(frame["record_id"])

    assert received_ids == expected_ids

    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait(timeout=3)


async def test_changes_ws_since_replays_preconnect_record(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """?since=<cursor> delivers records created after that cursor but before the
    current connection was established (pre-connect / offline events).

    Simulates a client that persists its last-seen stream_id across sessions.
    On reconnect it supplies the stored cursor so it receives everything that
    happened while it was fully offline — including events created before any
    WebSocket was open.

    Note: we use a precise tail cursor rather than the stream origin (0-0) to
    avoid scanning the full shared Redis stream, which grows with every test in
    the session and would make the test O(sessions) slow.
    """
    # Briefly connect to capture the current stream tail, then disconnect so
    # we have a cursor that is definitively *before* our test record.
    probe = _ws_communicator(test_app, notes_pod.pod_id, fixed_test_user["token"])
    await probe.send_input({"type": "websocket.connect"})
    ready = await _expect_accept_and_ready(probe)
    tail_cursor = ready["since"]
    await probe.send_input({"type": "websocket.disconnect", "code": 1000})
    await probe.wait(timeout=3)

    # Create a record while fully offline.
    record = await notes_pod.create_record("notes", {"body": "pre-connect"})

    # Reconnect with the cursor snapshotted before the record was written.
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"], since=tail_cursor
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    # The pre-connect record must be replayed.
    frame = await _recv_json(communicator, timeout=15)
    assert frame["type"] == "datastore.record.insert"
    assert frame["record_id"] == record["id"]
    assert frame["payload"]["body"] == "pre-connect"

    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait(timeout=3)
