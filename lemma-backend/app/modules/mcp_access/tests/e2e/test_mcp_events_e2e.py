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

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

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
        #: Answer every event with a 500, as a receiver that has gone would.
        self.failing = False

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
        if self.failing:
            return JSONResponse({"error": "down"}, status_code=500)
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
    tokens = await _connect(
        mcp_client,
        authenticated_client,
        pod_id,
        "pod:read pod:events",
        answer={"allow": True, "events": True},
    )
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
    tokens = await _connect(
        mcp_client,
        authenticated_client,
        pod_id,
        "pod:read pod:events",
        answer={"allow": True, "events": True},
    )
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


async def test_a_connection_not_allowed_events_cannot_subscribe(
    mcp_client, authenticated_client, test_pod, receiver
):
    """The app asked for events and the person left the box unticked: it can
    read, and it is never sent a row."""
    pod_id = test_pod["id"]
    await mcp_client.register()
    tokens = await _connect(
        mcp_client,
        authenticated_client,
        pod_id,
        "pod:read pod:events",
        answer={"allow": True},
    )
    assert "pod:events" not in tokens["scope"].split()
    table, _ = await _table_with_a_row(authenticated_client, pod_id)

    refused = await _rpc(
        mcp_client,
        pod_id,
        tokens["access_token"],
        "events/subscribe",
        {
            "name": "record.created",
            "arguments": {"table": table},
            "delivery": {"mode": "webhook", "url": receiver.url, "secret": SECRET},
        },
    )

    assert refused["error"]["code"] == -32012, refused
    assert refused["error"]["data"] == {"scope": "pod:events"}
    assert receiver.verifications == []


def _session_factory():
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

    return SessionUnitOfWorkFactory(async_session_maker)


async def _subscribed(mcp_client, authenticated_client, pod_id, receiver):
    """A connection allowed events, subscribed to a new table's rows."""
    await mcp_client.register()
    tokens = await _connect(
        mcp_client,
        authenticated_client,
        pod_id,
        "pod:read pod:events",
        answer={"allow": True, "events": True},
    )
    table, record_id = await _table_with_a_row(authenticated_client, pod_id)
    params = {
        "name": "record.created",
        "arguments": {"table": table},
        "delivery": {"mode": "webhook", "url": receiver.url, "secret": SECRET},
    }
    subscribed = await _rpc(
        mcp_client, pod_id, tokens["access_token"], "events/subscribe", params
    )
    assert "result" in subscribed, subscribed
    return tokens["access_token"], params, subscribed["result"]["id"], table, record_id


def _job_for(subscription_id: str, pod_id: str, table: str, record_id: str):
    return DeliveryJob(
        subscription_id=subscription_id,
        pod_id=UUID(pod_id),
        table=table,
        record_id=record_id,
        event_id=f"evt_{uuid4().hex}",
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )


async def _listening(person, pod_id: str) -> list[dict]:
    listed = await person.get("/oauth/grants", params={"pod_id": pod_id})
    assert listed.status_code == 200, listed.text
    return [item for app in listed.json()["items"] for item in app["listens_to"]]


async def test_stop_holds_against_the_apps_refresh_until_resumed(
    mcp_client, authenticated_client, async_client_for_stranger, test_pod, receiver
):
    pod_id = test_pod["id"]
    token, params, sub_id, table, record_id = await _subscribed(
        mcp_client, authenticated_client, pod_id, receiver
    )
    grant_id = (await authenticated_client.get("/oauth/grants")).json()["items"][0][
        "grant_id"
    ]
    stop_url = f"/oauth/grants/{grant_id}/subscriptions/{sub_id}"

    not_theirs = await async_client_for_stranger.delete(stop_url)
    assert not_theirs.status_code == 404, "someone else's is not theirs to stop"

    stopped = await authenticated_client.delete(stop_url)
    assert stopped.status_code == 204, stopped.text
    [shown] = await _listening(authenticated_client, pod_id)
    assert shown["id"] == sub_id and shown["stopped_at"]

    refused = await _rpc(mcp_client, pod_id, token, "events/subscribe", params)
    assert refused["error"]["code"] == -32012, refused
    assert refused["error"]["data"] == {"reason": "stopped"}

    # Unsubscribing and subscribing again does not clear the person's Stop.
    unsub = {key: params[key] for key in ("name", "arguments")}
    unsub["delivery"] = {"mode": "webhook", "url": receiver.url}
    await _rpc(mcp_client, pod_id, token, "events/unsubscribe", unsub)
    again = await _rpc(mcp_client, pod_id, token, "events/subscribe", params)
    assert again["error"]["code"] == -32012, again

    delivery = EventDelivery(
        SqlDeliveryLedger(_session_factory()), read_record=read_record_for
    )
    job = _job_for(sub_id, pod_id, table, record_id)
    assert await delivery.deliver(job) is Outcome.DROPPED
    assert receiver.events == []

    resumed = await authenticated_client.post(stop_url + "/resume")
    assert resumed.status_code == 204, resumed.text
    renewed = await _rpc(mcp_client, pod_id, token, "events/subscribe", params)
    assert renewed["result"]["id"] == sub_id
    assert await delivery.deliver(job) is Outcome.SENT
    assert len(receiver.events) == 1


async def test_a_receiver_that_keeps_failing_is_paused_until_the_app_refreshes(
    mcp_client, authenticated_client, test_pod, receiver
):
    from app.modules.mcp_access.infrastructure.subscription_repository import (
        PAUSE_AFTER_FAILURES,
    )

    pod_id = test_pod["id"]
    token, params, sub_id, table, record_id = await _subscribed(
        mcp_client, authenticated_client, pod_id, receiver
    )
    delivery = EventDelivery(
        SqlDeliveryLedger(_session_factory()), read_record=read_record_for
    )
    receiver.failing = True
    # Counted by outcome, not by loop: a worker running in the same process
    # may deliver the table's first row through the stream as well, and that
    # failure counts toward the same pause.
    outcomes = [
        await delivery.deliver(_job_for(sub_id, pod_id, table, record_id))
        for _ in range(PAUSE_AFTER_FAILURES)
    ]
    assert set(outcomes) <= {Outcome.RETRY, Outcome.DROPPED}
    assert outcomes[: PAUSE_AFTER_FAILURES // 2] == [Outcome.RETRY] * (
        PAUSE_AFTER_FAILURES // 2
    ), "a few failures are retried, not paused on"

    paused = _job_for(sub_id, pod_id, table, record_id)
    assert await delivery.deliver(paused) is Outcome.DROPPED, "nothing more is sent"
    [shown] = await _listening(authenticated_client, pod_id)
    assert shown["paused_at"] and shown["last_error"] == "http_500"

    receiver.failing = False
    refreshed = await _rpc(mcp_client, pod_id, token, "events/subscribe", params)
    assert "result" in refreshed, refreshed
    assert await delivery.deliver(paused) is Outcome.SENT
    [shown] = await _listening(authenticated_client, pod_id)
    assert shown["paused_at"] is None and shown["last_error"] is None


@contextlib.contextmanager
def _change_log_on():
    from app.modules.datastore.changes.gate import reset_change_log_gate
    from app.modules.datastore.config import datastore_settings

    previous = datastore_settings.datastore_change_log_mode
    datastore_settings.datastore_change_log_mode = "on"
    reset_change_log_gate()
    try:
        yield
    finally:
        datastore_settings.datastore_change_log_mode = previous
        reset_change_log_gate()


async def _run(*classes) -> int:
    """Hand watched writes' wakes off, then run ``classes`` until idle."""
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.work.testing import run_until_idle
    from app.modules.datastore.changes.handoff import ChangeHandoff
    from app.modules.datastore.infrastructure.session import get_datastore_engine

    await ChangeHandoff(get_datastore_engine(), async_session_maker).drain()
    return await run_until_idle(async_session_maker, classes=list(classes))


async def _add_rows(person, pod_id: str, table: str, count: int) -> list[str]:
    ids = []
    for index in range(count):
        row = await person.post(
            f"/pods/{pod_id}/datastore/tables/{table}/records",
            json={"data": {"company": f"Company {index}"}},
        )
        assert row.status_code in (200, 201), row.text
        ids.append(str(row.json().get("id")))
    return ids


async def _owed(sub_id: str) -> list[tuple[str, str | None]]:
    from sqlalchemy import text

    async with _session_factory()() as uow:
        rows = await uow.session.execute(
            text(
                "SELECT d.record_id, d.outcome FROM mcp_event_deliveries d "
                "JOIN mcp_event_subscriptions s ON s.id = d.subscription_id "
                "WHERE s.public_id = :id ORDER BY d.created_at"
            ),
            {"id": sub_id},
        )
        return [(str(r[0]), r[1]) for r in rows]


async def test_new_rows_reach_the_receiver_once_through_the_change_log(
    mcp_client, authenticated_client, test_pod, receiver
):
    """The path a real insert takes: the subscription is a change-log
    subscriber; each new row in its table is one delivery row and one
    ``mcp.deliver`` item, however often the page is delivered; a send whose
    completion is lost is sent again under the same id."""
    from app.core.infrastructure.work import WorkClass
    from app.modules.mcp_access.events import event_deliveries

    with _change_log_on():
        pod_id = test_pod["id"]
        _, _, sub_id, table, _ = await _subscribed(
            mcp_client, authenticated_client, pod_id, receiver
        )
        await _run(WorkClass.EVENTS)  # the subscriber row, at the horizon
        from app.modules.datastore.changes.subscribers import SubscriberTable

        start = await SubscriberTable(UUID(pod_id)).get(f"mcp:{sub_id}")
        assert start is not None
        added = await _add_rows(authenticated_client, pod_id, table, 3)
        await _run(WorkClass.EVENTS)
        assert [record for record, _ in await _owed(sub_id)] == added
        await _run(WorkClass.DELIVERY)
        # Sent concurrently, so in no particular order.
        assert sorted(e["data"]["record_id"] for e in receiver.events) == sorted(added)
        ids = [event["_headers"]["webhook-id"] for event in receiver.events]
        assert len(set(ids)) == 3 and all(i.startswith("chg_") for i in ids)
        assert all(
            event["_headers"]["subscription"] == sub_id for event in receiver.events
        )

        # The page delivered again from the start: nothing is owed twice.
        from app.modules.datastore.tests.e2e.change_log_support import rewind

        await rewind(UUID(pod_id), f"mcp:{sub_id}", to=start.cursor)
        await _run(WorkClass.EVENTS, WorkClass.DELIVERY)
        assert len(await _owed(sub_id)) == 3 and len(receiver.events) == 3

        # The receiver took the fourth, then the worker died before its
        # completion committed: the state that leaves is an unsettled delivery
        # with its item still owed. It is sent again with the same id, and is
        # owed once.
        from sqlalchemy import text

        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.work import WorkWriter, pod_namespace

        [fourth] = await _add_rows(authenticated_client, pod_id, table, 1)
        await _run(WorkClass.EVENTS, WorkClass.DELIVERY)
        async with async_session_maker() as session:
            delivery_id = await session.scalar(
                text(
                    "UPDATE mcp_event_deliveries SET delivered_at = NULL, outcome = NULL "
                    "WHERE record_id = :record RETURNING id"
                ),
                {"record": fourth},
            )
            await WorkWriter(session).add(
                event_deliveries.DELIVER,
                pod_namespace(UUID(pod_id)),
                event_deliveries.DeliveryRef(delivery_id=delivery_id),
                rate_key=f"mcp:{sub_id}",
            )
            await session.commit()
        await _run(WorkClass.DELIVERY)
        again = [e for e in receiver.events if e["data"]["record_id"] == fourth]
        assert len(again) == 2
        assert again[0]["_headers"]["webhook-id"] == again[1]["_headers"]["webhook-id"]
        owed = await _owed(sub_id)
        assert [record for record, _ in owed] == [*added, fourth]
        assert {outcome for _, outcome in owed} == {"sent"}


async def test_deliveries_past_a_subscriptions_rate_wait_and_are_not_charged(
    mcp_client, authenticated_client, test_pod, receiver
):
    from sqlalchemy import text

    from app.core.infrastructure.work import WorkClass

    with _change_log_on():
        pod_id = test_pod["id"]
        _, _, sub_id, table, _ = await _subscribed(
            mcp_client, authenticated_client, pod_id, receiver
        )
        await _run(WorkClass.EVENTS)
        await _add_rows(authenticated_client, pod_id, table, 5)
        await _run(WorkClass.EVENTS)
        async with _session_factory()() as uow:
            await uow.session.execute(
                text(
                    "UPDATE work_queues SET rates = COALESCE(rates, '{}'::jsonb) || "
                    '\'{"mcp_delivery": {"limit": 2, "per_seconds": 3600}}\'::jsonb '
                    "WHERE class = 'delivery' AND ns = :ns"
                ),
                {"ns": f"pod:{pod_id}"},
            )
            await uow.commit()
        await _run(WorkClass.DELIVERY)
        assert len(receiver.events) == 2, "two tokens, two sends"
        async with _session_factory()() as uow:
            waiting = (
                await uow.session.execute(
                    text(
                        "SELECT state, attempts, run_at > now() FROM work_items "
                        "WHERE kind = 'mcp.deliver' AND ns = :ns"
                    ),
                    {"ns": f"pod:{pod_id}"},
                )
            ).all()
        assert [tuple(row) for row in waiting] == [("queued", 0, True)] * 3


async def test_the_sweep_removes_what_can_never_deliver_again(
    mcp_client, authenticated_client, test_pod, receiver
):
    from datetime import timedelta

    from sqlalchemy import update

    from app.modules.mcp_access.infrastructure.models import McpEventSubscription
    from app.modules.mcp_access.infrastructure.subscription_repository import (
        EventSubscriptionRepository,
    )

    pod_id = test_pod["id"]
    _, _, sub_id, _, _ = await _subscribed(
        mcp_client, authenticated_client, pod_id, receiver
    )
    now = datetime.now(timezone.utc)
    async with _session_factory()() as uow:
        await uow.session.execute(
            update(McpEventSubscription)
            .where(McpEventSubscription.public_id == sub_id)
            .values(refresh_before=now - timedelta(days=8))
        )
        await uow.commit()

    assert await _listening(authenticated_client, pod_id) == [], (
        "a lapsed subscription is not shown as listening"
    )
    async with _session_factory()() as uow:
        repository = EventSubscriptionRepository(uow)
        assert await repository.sweep(lapsed_before=now - timedelta(days=7), batch=50)
        await uow.commit()
    async with _session_factory()() as uow:
        assert await EventSubscriptionRepository(uow).get(sub_id) is None


async def test_the_sweep_removes_a_revoked_connections_subscriptions(
    mcp_client, authenticated_client, test_pod, receiver
):
    """Revoking keeps the grant row, so its CASCADE never fires; the sweep is
    what removes what the connection subscribed to."""
    from datetime import timedelta

    from app.modules.mcp_access.infrastructure.subscription_repository import (
        EventSubscriptionRepository,
    )

    pod_id = test_pod["id"]
    _, _, sub_id, _, _ = await _subscribed(
        mcp_client, authenticated_client, pod_id, receiver
    )
    grant_id = (await authenticated_client.get("/oauth/grants")).json()["items"][0][
        "grant_id"
    ]
    revoked = await authenticated_client.delete(f"/oauth/grants/{grant_id}")
    assert revoked.status_code in (200, 204), revoked.text

    now = datetime.now(timezone.utc)
    async with _session_factory()() as uow:
        assert await EventSubscriptionRepository(uow).sweep(
            lapsed_before=now - timedelta(days=7), batch=50
        )
        await uow.commit()
    async with _session_factory()() as uow:
        assert await EventSubscriptionRepository(uow).get(sub_id) is None


async def test_a_paused_subscription_holds_none_of_the_pods_log(
    mcp_client, authenticated_client, test_pod, receiver
):
    """Paused by failures, a subscription gives up its place in the change
    log: retention trims what is written meanwhile, and the refresh that
    resumes it watches from then on -- the rows written while it was paused
    are never sent."""
    from sqlalchemy import text

    from app.core.infrastructure.work import WorkClass
    from app.modules.datastore.changes.retention import retain
    from app.modules.datastore.changes.schema import changes_table
    from app.modules.datastore.changes.subscribers import SubscriberTable
    from app.modules.datastore.infrastructure.pod_schema_migrations import (
        schema_name_for_pod,
    )
    from app.modules.datastore.infrastructure.session import get_datastore_engine
    from app.modules.datastore.tests.e2e.change_log_support import log_final
    from app.modules.mcp_access.infrastructure.subscription_repository import (
        PAUSE_AFTER_FAILURES,
        EventSubscriptionRepository,
    )

    with _change_log_on():
        pod_id = test_pod["id"]
        token, params, sub_id, table, _ = await _subscribed(
            mcp_client, authenticated_client, pod_id, receiver
        )
        await _run(WorkClass.EVENTS)
        subscribers = SubscriberTable(UUID(pod_id))
        assert await subscribers.get(f"mcp:{sub_id}") is not None

        now = datetime.now(timezone.utc)
        async with _session_factory()() as uow:
            repository = EventSubscriptionRepository(uow)
            for _ in range(PAUSE_AFTER_FAILURES):
                await repository.record_delivery(sub_id, now=now, error="http_500")
            await uow.commit()
        await _run(WorkClass.EVENTS)  # the sync the pause asked for
        assert await subscribers.get(f"mcp:{sub_id}") is None

        missed = await _add_rows(authenticated_client, pod_id, table, 3)
        await _run(WorkClass.EVENTS)
        assert not {record for record, _ in await _owed(sub_id)} & set(missed)
        # Old enough to go, and nobody holds it: retention takes it.
        await log_final()
        async with get_datastore_engine().begin() as conn:
            await conn.execute(
                text(
                    f"UPDATE {changes_table(schema_name_for_pod(UUID(pod_id)))} "
                    "SET at = now() - interval '2 hours'"
                )
            )
        assert await retain(UUID(pod_id), await subscribers.all()) >= 3

        refreshed = await _rpc(mcp_client, pod_id, token, "events/subscribe", params)
        assert "result" in refreshed, refreshed
        await _run(WorkClass.EVENTS)
        assert await subscribers.get(f"mcp:{sub_id}") is not None
        [after] = await _add_rows(authenticated_client, pod_id, table, 1)
        # Read below the cluster's oldest open transaction: in a shared CI run
        # another test's can hold the new row back past one delivery pass.
        await log_final()
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.work.testing import make_due

        await make_due(async_session_maker, kind="changes.deliver")
        await _run(WorkClass.EVENTS)
        owed = [record for record, _ in await _owed(sub_id)]
        assert after in owed and not set(owed) & set(missed)
