"""An outside client subscribes to a pod's events, the way ChatGPT does.

Over HTTP on the public mount, at protocol 2026-07-28: `server/discover` says
`events`, `events/list` names `record.created`, `events/subscribe` verifies
the callback with a signed challenge before storing anything, a new row reaches
the callback signed with the client's own secret, and revoking the connection
stops the next delivery -- which ChatGPT itself would never learn, because it
does not handle `terminated`.

The receiver is a real HTTP server on a free port that checks every signature
it is sent with the client's secret, as ChatGPT's would.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import socket
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.webhooks.signatures import svix_signature_matches
from app.modules.test_support.e2e.waiters import eventually
from app.modules.mcp_access.services.event_delivery import (
    DeliveryJob,
    EventDelivery,
    Outcome,
    SqlDeliveryLedger,
    read_record_for,
)
from app.modules.mcp_access.tests.e2e.test_mcp_oauth_flow_e2e import (
    _connect,
    _pod_mcp_running,
    _Client,
)

pytestmark = pytest.mark.e2e

SECRET = "whsec_" + base64.b64encode(os.urandom(32)).decode()
PROTOCOL = "2026-07-28"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Receiver:
    def __init__(self) -> None:
        self.verifications: list[dict] = []
        self.events: list[dict] = []
        self.bad_signatures = 0

    async def hook(self, request: Request) -> JSONResponse:
        body = await request.body()
        signed = svix_signature_matches(
            request.headers.get("webhook-signature"),
            request.headers.get("webhook-id") or "",
            request.headers.get("webhook-timestamp"),
            body,
            [SECRET],
        )
        if not signed:
            self.bad_signatures += 1
            return JSONResponse({"error": "bad signature"}, status_code=401)
        payload = json.loads(body)
        if payload.get("type") == "verification":
            self.verifications.append(payload)
            return JSONResponse({"challenge": payload["challenge"]})
        payload["_headers"] = {
            "webhook-id": request.headers.get("webhook-id"),
            "subscription": request.headers.get("x-mcp-subscription-id"),
        }
        self.events.append(payload)
        return JSONResponse({"ok": True})


@pytest.fixture
async def receiver(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
    hooks = _Receiver()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            Starlette(routes=[Route("/hook", hooks.hook, methods=["POST"])]),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    task = asyncio.create_task(server.serve())

    async def started() -> bool:
        if task.done():
            raise RuntimeError(f"receiver failed to start: {task.exception()}")
        return server.started

    await eventually(
        label=f"receiver on port {port}",
        probe=started,
        done=bool,
        timeout_seconds=5.0,
        interval_seconds=0.05,
    )
    hooks.url = f"http://127.0.0.1:{port}/hook"  # type: ignore[attr-defined]
    yield hooks
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture
async def mcp_client(test_app):
    from httpx import ASGITransport, AsyncClient

    async with _pod_mcp_running(test_app):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as http:
            yield _Client(http)


async def _rpc(client: _Client, pod_id: str, token: str, method: str, params=None):
    """A 2026-07-28 request: the version and method ride in headers and in
    the envelope's `_meta`, as a modern client sends them."""
    envelope = dict(params or {})
    envelope["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    response = await client.http.post(
        f"/mcp/{pod_id}",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Method": method,
            "Authorization": f"Bearer {token}",
        },
        json={"jsonrpc": "2.0", "id": 9, "method": method, "params": envelope},
    )
    return response.json()


async def _table_with_a_row(person, pod_id: str) -> tuple[str, str]:
    table = f"leads_{uuid4().hex[:6]}"
    made = await person.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table,
            "enable_rls": False,
            "columns": [{"name": "company", "type": "TEXT"}],
        },
    )
    assert made.status_code == 201, made.text
    row = await person.post(
        f"/pods/{pod_id}/datastore/tables/{table}/records",
        json={"data": {"company": "Northwind"}},
    )
    assert row.status_code in (200, 201), row.text
    body = row.json()
    record_id = str(body.get("id") or body.get("data", {}).get("id"))
    return table, record_id


async def test_a_client_subscribes_and_a_new_row_reaches_it_signed(
    mcp_client, authenticated_client, test_pod, receiver, db_session
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    token = tokens["access_token"]

    discovered = await _rpc(mcp_client, pod_id, token, "server/discover")
    assert "events" in discovered["result"]["capabilities"], discovered

    listed = await _rpc(mcp_client, pod_id, token, "events/list")
    assert [event["name"] for event in listed["result"]["events"]] == ["record.created"]

    table, record_id = await _table_with_a_row(authenticated_client, pod_id)
    subscribed = await _rpc(
        mcp_client,
        pod_id,
        token,
        "events/subscribe",
        {
            "name": "record.created",
            "arguments": {"table": table},
            "delivery": {"mode": "webhook", "url": receiver.url, "secret": SECRET},
            "cursor": None,
            "ttlMs": 3_600_000,
        },
    )
    result = subscribed["result"]
    assert result["id"].startswith("sub_")
    assert result["refreshBefore"].endswith("Z")
    assert len(receiver.verifications) == 1, "the callback proves itself first"

    # Subscribing again with the same identity is a refresh, not a second one,
    # and a callback already proven is not challenged again.
    again = await _rpc(
        mcp_client,
        pod_id,
        token,
        "events/subscribe",
        {
            "name": "record.created",
            "arguments": {"table": table},
            "delivery": {"mode": "webhook", "url": receiver.url, "secret": SECRET},
        },
    )
    assert again["result"]["id"] == result["id"]
    assert len(receiver.verifications) == 1

    delivery = EventDelivery(
        SqlDeliveryLedger(_session_factory()), read_record=read_record_for
    )
    job = DeliveryJob(
        subscription_id=result["id"],
        pod_id=UUID(pod_id),
        table=table,
        record_id=record_id,
        event_id=f"evt_{uuid4().hex}",
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )
    assert await delivery.deliver(job) is Outcome.SENT
    [event] = receiver.events
    assert event["name"] == "record.created"
    assert event["eventId"] == job.event_id
    assert event["_headers"] == {
        "webhook-id": job.event_id,
        "subscription": result["id"],
    }
    assert event["data"]["table"] == table
    assert event["data"]["record"]["company"] == "Northwind"
    assert receiver.bad_signatures == 0

    # The person takes the connection back. ChatGPT would not hear about it;
    # the next delivery is where it stops.
    grants = (await authenticated_client.get("/oauth/grants")).json()
    grant_id = grants["items"][0]["grant_id"]
    revoked = await authenticated_client.delete(f"/oauth/grants/{grant_id}")
    assert revoked.status_code in (200, 204), revoked.text
    assert await delivery.deliver(job) is Outcome.DROPPED
    assert len(receiver.events) == 1


async def test_a_table_the_person_cannot_read_is_refused(
    mcp_client, authenticated_client, test_pod, receiver
):
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(mcp_client, authenticated_client, pod_id, "pod:read")
    refused = await _rpc(
        mcp_client,
        pod_id,
        tokens["access_token"],
        "events/subscribe",
        {
            "name": "record.created",
            "arguments": {"table": "no_such_table"},
            "delivery": {"mode": "webhook", "url": receiver.url, "secret": SECRET},
        },
    )
    assert refused["error"]["code"] == -32012, refused
    assert receiver.verifications == [], "nothing is sent to a refused callback"

    bad_secret = await _rpc(
        mcp_client,
        pod_id,
        tokens["access_token"],
        "events/subscribe",
        {
            "name": "record.created",
            "arguments": {"table": "anything"},
            "delivery": {"mode": "webhook", "url": receiver.url, "secret": "short"},
        },
    )
    assert bad_secret["error"]["code"] == -32602, bad_secret

    gone = await _rpc(
        mcp_client,
        pod_id,
        tokens["access_token"],
        "events/unsubscribe",
        {
            "name": "record.created",
            "arguments": {"table": "anything"},
            "delivery": {"mode": "webhook", "url": receiver.url},
        },
    )
    assert gone["result"] == {} or "_meta" in gone["result"], "idempotent"


def _session_factory():
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

    return SessionUnitOfWorkFactory(async_session_maker)
