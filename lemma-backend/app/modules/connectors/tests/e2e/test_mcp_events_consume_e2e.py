"""Standing work on a connected MCP server's events, against a live server.

The server is a real FastMCP app on a free port, offering one tool and the
working-group draft's `events/*`, and behaving the way a subscriber should be
able to rely on: it proves the callback with a signed challenge before
answering `events/subscribe`, signs every occurrence with the secret it was
given, and treats a second subscribe with the same identity as a refresh.

Its deliveries reach the app under test in process, so the challenge arrives
at `POST /webhooks/mcp` while `events/subscribe` is still waiting -- the
order that forces the subscription to be committed before the call.

The loop: connecting the server records its events; the pod's catalog offers
them; a WEBHOOK schedule on one subscribes on the author's account; an
occurrence starts one run and its redelivery none; an edit swaps the
subscription; renewal refreshes it; deleting the schedule unsubscribes, and the
old callback stops verifying.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.context import ServerRequestContext
from fastmcp.server.extensions import MethodBinding, ServerExtension
from httpx import ASGITransport, AsyncClient
from pydantic import JsonValue
from sqlalchemy import select

from app.core.webhooks.signatures import standard_webhook_signature
from app.mcp_events import ListEventsParams, SubscribeParams, UnsubscribeParams
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.mcp_event import (
    ConnectorEventSubscription,
)
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

PROTOCOL = frozenset({"2026-07-28"})

ISSUE_CREATED = {
    "name": "issue.created",
    "description": "An issue was opened in a project.",
    "delivery": ["webhook"],
    "inputSchema": {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
        "additionalProperties": False,
    },
    "payloadSchema": {"type": "object"},
}


def _digest(event_id: str) -> str:
    return hashlib.sha256(event_id.encode()).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class _Subscription:
    id: str
    name: str
    arguments: dict[str, JsonValue]
    url: str
    secret: str
    refreshes: int = 0


@dataclass
class _Tracker:
    """The server's side: what it was asked, and what it sends."""

    deliver: object = None
    subscriptions: dict[tuple[str, str, str], _Subscription] = field(
        default_factory=dict
    )
    challenges_passed: int = 0
    unsubscribed: list[str] = field(default_factory=list)
    #: Projects whose subscriptions it will no longer refresh.
    refusing: set[str] = field(default_factory=set)

    async def send(self, sub: _Subscription, body: dict[str, object]) -> tuple:
        raw = json.dumps(body).encode()
        message_id = f"msg_{secrets.token_hex(6)}"
        timestamp = int(time.time())
        return await self.deliver(  # type: ignore[misc]
            sub.url,
            raw,
            {
                "Content-Type": "application/json",
                "webhook-id": message_id,
                "webhook-timestamp": str(timestamp),
                "webhook-signature": standard_webhook_signature(
                    sub.secret, message_id, timestamp, raw
                ),
                "X-MCP-Subscription-Id": sub.id,
            },
        )

    async def emit(self, event_id: str, project: str, title: str) -> list[int]:
        statuses = []
        for sub in list(self.subscriptions.values()):
            if sub.arguments.get("project") != project:
                continue
            status, _ = await self.send(
                sub,
                {
                    "eventId": event_id,
                    "name": "issue.created",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {"project": project, "title": title},
                    "cursor": None,
                },
            )
            statuses.append(status)
        return statuses


class _TrackerEvents(ServerExtension):
    identifier = "test.tracker/events"

    def __init__(self, tracker: _Tracker) -> None:
        self._tracker = tracker

    def methods(self) -> list[MethodBinding]:
        return [
            MethodBinding("events/list", ListEventsParams, self._list, PROTOCOL),
            MethodBinding(
                "events/subscribe", SubscribeParams, self._subscribe_hook, PROTOCOL
            ),
            MethodBinding(
                "events/unsubscribe",
                UnsubscribeParams,
                self._unsubscribe_hook,
                PROTOCOL,
            ),
        ]

    async def _list(
        self, ctx: ServerRequestContext[object, object], params: ListEventsParams
    ) -> dict[str, JsonValue]:
        return {"events": [ISSUE_CREATED]}

    async def _subscribe_hook(
        self, ctx: ServerRequestContext[object, object], params: SubscribeParams
    ) -> dict[str, JsonValue]:
        from mcp.shared.exceptions import MCPError

        key = (
            params.name,
            json.dumps(params.arguments, sort_keys=True),
            params.delivery.url,
        )
        existing = self._tracker.subscriptions.get(key)
        if existing is not None:
            if existing.arguments.get("project") in self._tracker.refusing:
                raise MCPError(code=-32016, message="subscription revoked")
            existing.refreshes += 1
            if params.delivery.secret is not None:
                existing.secret = params.delivery.secret.get_secret_value()
        else:
            sub = _Subscription(
                id=f"sub_{secrets.token_hex(6)}",
                name=params.name,
                arguments=dict(params.arguments),
                url=params.delivery.url,
                secret=(
                    params.delivery.secret.get_secret_value()
                    if params.delivery.secret is not None
                    else ""
                ),
            )
            challenge = secrets.token_urlsafe(16)
            status, body = await self._tracker.send(
                sub, {"type": "verification", "challenge": challenge}
            )
            if status != 200 or body.get("challenge") != challenge:
                raise MCPError(code=-32015, message="callback did not verify")
            self._tracker.challenges_passed += 1
            self._tracker.subscriptions[key] = sub
            existing = sub
        refresh_before = datetime.now(timezone.utc) + timedelta(hours=1)
        return {
            "id": existing.id,
            "refreshBefore": refresh_before.isoformat().replace("+00:00", "Z"),
        }

    async def _unsubscribe_hook(
        self, ctx: ServerRequestContext[object, object], params: UnsubscribeParams
    ) -> dict[str, JsonValue]:
        key = (
            params.name,
            json.dumps(params.arguments, sort_keys=True),
            params.delivery.url,
        )
        gone = self._tracker.subscriptions.pop(key, None)
        if gone is not None:
            self._tracker.unsubscribed.append(gone.id)
        return {}


@pytest_asyncio.fixture
async def tracker(test_app, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
    state = _Tracker()
    base = settings.api_url.rstrip("/")
    inbound = AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    )

    async def deliver(url: str, raw: bytes, headers: dict[str, str]) -> tuple:
        assert url.startswith(base), url
        response = await inbound.post(
            url.removeprefix(base), content=raw, headers=headers
        )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {}

    state.deliver = deliver
    server = FastMCP("tracker")

    @server.tool
    def open_issue(project: str, title: str) -> dict:
        """Open an issue."""
        return {"project": project, "title": title}

    server.add_extension(_TrackerEvents(state))
    port = _free_port()
    runner = uvicorn.Server(
        uvicorn.Config(
            server.http_app(
                path="/mcp", transport="http", json_response=True, stateless_http=True
            ),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    task = asyncio.create_task(runner.serve())

    async def started() -> bool:
        if task.done():
            raise RuntimeError(f"tracker failed to start: {task.exception()}")
        return runner.started

    await eventually(
        label=f"tracker on {port}",
        probe=started,
        done=bool,
        timeout_seconds=5.0,
        interval_seconds=0.05,
    )
    state.url = f"http://127.0.0.1:{port}/mcp"  # type: ignore[attr-defined]
    yield state
    runner.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await inbound.aclose()


@pytest_asyncio.fixture
async def tracker_account(db_session, fixed_test_org, fixed_test_user, tracker):
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.api.dependencies import get_connector_service

    connector = Connector(
        id=f"tracker-{uuid4().hex[:8]}",
        title="Tracker",
        description="An issue tracker that speaks MCP Events.",
        kinds=[
            {
                "kind": "mcp",
                "auth_scheme": "API_KEY",
                "discovery": "mcp",
                "auth_config_schema": {
                    "type": "object",
                    "required": ["server_url"],
                    "properties": {"server_url": {"type": "string"}},
                    "additionalProperties": False,
                },
            }
        ],
        is_active=True,
    )
    db_session.add(connector)
    await db_session.commit()
    org_id = UUID(str(fixed_test_org["id"]))
    user_id = UUID(str(fixed_test_user["id"]))
    service = get_connector_service(SqlAlchemyUnitOfWork(db_session))
    install = await service.create_auth_config(
        user_id=user_id,
        organization_id=org_id,
        connector_id=connector.id,
        config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
        config={"server_url": tracker.url},
        name=f"tracker-{uuid4().hex[:6]}",
    )
    account = await service.create_account(
        user_id=user_id,
        organization_id=org_id,
        auth_config_id=install.id,
        credentials={"api_key": "unused"},
    )
    return install, account


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Tracker pod {uuid4().hex[:6]}",
            "organization_id": org_id,
            "type": "HYBRID",
            "description": "MCP events e2e pod",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _schedule(
    client: AsyncClient, pod_id: str, account_id: str, *, project: str, status=201
) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "WEBHOOK",
            "agent_name": "POD_DEFAULT",
            "instruction": "Triage the new issue.",
            "account_id": account_id,
            "config": {
                "source": "mcp",
                "event": "issue.created",
                "arguments": {"project": project},
            },
        },
    )
    assert response.status_code == status, response.text
    return response.json()


async def _runs(db_session, schedule_id: str) -> list[ScheduleRun]:
    db_session.expire_all()
    return list(
        (
            await db_session.execute(
                select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id)
            )
        )
        .scalars()
        .all()
    )


@dataclass(frozen=True)
class _Row:
    id: UUID
    remote_id: str | None
    refresh_before: datetime | None


async def _subscriptions(db_session) -> list[_Row]:
    """Read as values: the session is expired between reads, and an expired
    row reloading itself outside the greenlet is not what is being tested."""
    db_session.expire_all()
    rows = (await db_session.execute(select(ConnectorEventSubscription))).scalars()
    return [_Row(row.id, row.remote_id, row.refresh_before) for row in rows]


async def test_an_issue_on_a_connected_server_starts_standing_work_once(
    authenticated_client, fixed_test_org, db_session, tracker, tracker_account, worker
):
    _ = worker
    install, account = tracker_account
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])

    catalog = (await authenticated_client.get(f"/pods/{pod_id}/events")).json()
    offered = [item for item in catalog["items"] if item.get("event")]
    assert [
        (item["event"], item["server"], item["schedule_type"]) for item in offered
    ] == [("issue.created", install.name, "WEBHOOK")]
    assert offered[0]["account_id"] == str(account.id)

    schedule = await _schedule(
        authenticated_client, pod_id, str(account.id), project="web"
    )
    assert tracker.challenges_passed == 1, "the callback proved itself first"
    [row] = await _subscriptions(db_session)
    assert schedule["config"]["provider_trigger_id"] == str(row.id)
    assert row.remote_id and row.refresh_before is not None
    assert schedule["listening"]["state"] == "listening"

    # A schedule in another pod carrying that subscription id with no account
    # behind it -- however it got there: an import, a path that skipped the
    # API's checks, a row from before they existed. Nothing the server sends
    # for this subscription reaches it.
    from sqlalchemy import update as sql_update

    from app.modules.schedule.infrastructure.models.schedule import Schedule

    other_pod = await _pod(authenticated_client, fixed_test_org["id"])
    forged = await _schedule(
        authenticated_client, other_pod, str(account.id), project="elsewhere"
    )
    await db_session.execute(
        sql_update(Schedule)
        .where(Schedule.id == forged["id"])
        .values(account_id=None, config={"provider_trigger_id": str(row.id)})
    )
    await db_session.commit()

    assert await tracker.emit("evt_1", "web", "Login is broken") == [200]
    runs = await eventually(
        label="one run",
        probe=lambda: _runs(db_session, schedule["id"]),
        done=lambda found: len(found) == 1,
        timeout_seconds=30,
        interval_seconds=0.15,
    )
    assert runs[0].source_event_id == f"mcp:{row.id}:" + _digest("evt_1")

    # The same event again, then a new one: only the new one runs.
    assert await tracker.emit("evt_1", "web", "Login is broken") == [200]
    assert await tracker.emit("evt_2", "web", "Signup is slow") == [200]
    runs = await eventually(
        label="two runs",
        probe=lambda: _runs(db_session, schedule["id"]),
        done=lambda found: len(found) >= 2,
        timeout_seconds=30,
        interval_seconds=0.15,
    )
    assert sorted(run.source_event_id for run in runs) == [
        f"mcp:{row.id}:" + digest
        for digest in sorted([_digest("evt_1"), _digest("evt_2")])
    ]
    assert await _runs(db_session, forged["id"]) == []


async def test_edit_renew_and_delete_keep_the_server_in_step(
    authenticated_client, fixed_test_org, db_session, tracker, tracker_account
):
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.connectors.contracts.mcp_events import mcp_target
    from app.modules.connectors.services.mcp_event_subscriptions import (
        McpEventSubscriptions,
    )

    _, account = tracker_account
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    schedule = await _schedule(
        authenticated_client, pod_id, str(account.id), project="web"
    )
    [first] = await _subscriptions(db_session)

    edited = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={
            "config": {
                "source": "mcp",
                "event": "issue.created",
                "arguments": {"project": "api"},
            }
        },
    )
    assert edited.status_code == 200, edited.text
    [second] = await eventually(
        label="the old subscription dropped",
        probe=lambda: _subscriptions(db_session),
        done=lambda found: [row.id for row in found] != [first.id] and len(found) == 1,
        timeout_seconds=10,
        interval_seconds=0.1,
    )
    assert edited.json()["config"]["provider_trigger_id"] == str(second.id)
    assert first.remote_id in tracker.unsubscribed
    assert [sub.arguments for sub in tracker.subscriptions.values()] == [
        {"project": "api"}
    ]

    later = datetime.now(timezone.utc) + timedelta(minutes=40)
    renewed = await McpEventSubscriptions(
        SessionUnitOfWorkFactory(async_session_maker),
        target=mcp_target,
        clock=lambda: later,
    ).renew_due()
    assert renewed == 1
    assert [sub.refreshes for sub in tracker.subscriptions.values()] == [1]

    [live] = list(tracker.subscriptions.values())
    removed = await authenticated_client.delete(
        f"/pods/{pod_id}/schedules/{schedule['id']}"
    )
    assert removed.status_code in (200, 204), removed.text
    # Unsubscribed once the delete committed, not before it.
    await eventually(
        label="unsubscribed after the delete committed",
        probe=lambda: _subscriptions(db_session),
        done=lambda found: found == [] and tracker.subscriptions == {},
        timeout_seconds=10,
        interval_seconds=0.1,
    )
    status, _ = await tracker.send(
        live, {"eventId": "evt_late", "name": "issue.created", "data": {}}
    )
    assert status == 403, "a callback with no subscription verifies nothing"


async def test_a_subscription_the_server_keeps_refusing_steps_aside(
    authenticated_client,
    fixed_test_org,
    db_session,
    tracker,
    tracker_account,
    monkeypatch,
):
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.connectors.contracts.mcp_events import mcp_target
    from app.modules.connectors.infrastructure.repositories import (
        mcp_event_repository,
    )
    from app.modules.connectors.services.mcp_event_subscriptions import (
        McpEventSubscriptions,
    )

    # A batch of one shows with two subscriptions what a full batch of refused
    # ones did before: the refused one took the batch on every pass.
    monkeypatch.setattr(mcp_event_repository, "REFRESH_BATCH", 1)
    _, account = tracker_account
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    await _schedule(authenticated_client, pod_id, str(account.id), project="web")
    await _schedule(authenticated_client, pod_id, str(account.id), project="api")
    tracker.refusing.add("web")

    later = datetime.now(timezone.utc) + timedelta(minutes=40)
    refresher = McpEventSubscriptions(
        SessionUnitOfWorkFactory(async_session_maker),
        target=mcp_target,
        clock=lambda: later,
    )
    assert await refresher.renew_due() == 0, "the refused one was due first"
    assert await refresher.renew_due() == 1, "and then waited its turn"
    assert {
        sub.arguments["project"]: sub.refreshes
        for sub in tracker.subscriptions.values()
    } == {"web": 0, "api": 1}

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(
                ConnectorEventSubscription.arguments,
                ConnectorEventSubscription.renew_failures,
                ConnectorEventSubscription.renew_after,
                ConnectorEventSubscription.last_error,
            )
        )
    ).all()
    state = {row.arguments["project"]: row for row in rows}
    assert state["web"].renew_failures == 1
    assert state["web"].renew_after == later + timedelta(minutes=5)
    assert "subscription revoked" in (state["web"].last_error or "")
    assert state["api"].renew_failures == 0
    assert state["api"].last_error is None


async def test_what_cannot_be_listened_to_is_refused_and_leaves_nothing(
    authenticated_client, fixed_test_org, db_session, tracker, tracker_account
):
    _, account = tracker_account
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])

    no_account = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "WEBHOOK",
            "agent_name": "POD_DEFAULT",
            "instruction": "x",
            "config": {"source": "mcp", "event": "issue.created", "arguments": {}},
        },
    )
    assert no_account.status_code in (400, 422), no_account.text

    unknown = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "WEBHOOK",
            "agent_name": "POD_DEFAULT",
            "instruction": "x",
            "account_id": str(account.id),
            "config": {"source": "mcp", "event": "issue.closed", "arguments": {}},
        },
    )
    assert unknown.status_code in (400, 422), unknown.text

    unnarrowed = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "WEBHOOK",
            "agent_name": "POD_DEFAULT",
            "instruction": "x",
            "account_id": str(account.id),
            "config": {"source": "mcp", "event": "issue.created", "arguments": {}},
        },
    )
    assert unnarrowed.status_code in (400, 422), unnarrowed.text
    assert "project" in unnarrowed.text

    assert await _subscriptions(db_session) == []
    assert tracker.subscriptions == {}


def _reconciler(*, is_member=None, clock=None):
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.connectors.contracts.mcp_events import (
        mcp_listening_page,
        unsubscribe_from_mcp_event,
    )
    from app.modules.schedule.services.mcp_listening_reconciler import (
        McpListeningReconciler,
    )

    extra = {}
    if is_member is not None:
        extra["is_member"] = is_member
    if clock is not None:
        extra["clock"] = clock
    return McpListeningReconciler(
        SessionUnitOfWorkFactory(async_session_maker),
        page=mcp_listening_page,
        unsubscribe=unsubscribe_from_mcp_event,
        **extra,
    )


async def _schedule_row(db_session, schedule_id: str):
    from app.modules.schedule.infrastructure.models.schedule import Schedule

    db_session.expire_all()
    return (
        await db_session.execute(select(Schedule).where(Schedule.id == schedule_id))
    ).scalar_one_or_none()


async def test_a_subscription_no_schedule_holds_is_dropped(
    authenticated_client, fixed_test_org, db_session, tracker, tracker_account
):
    """A create whose request rolled back after subscribing, or a delete whose
    unsubscribe failed: the subscription renewed on the author's account with
    nothing listening. The reconciler drops it."""
    from sqlalchemy import delete

    from app.modules.schedule.infrastructure.models.schedule import Schedule

    _, account = tracker_account
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    schedule = await _schedule(
        authenticated_client, pod_id, str(account.id), project="web"
    )
    [row] = await _subscriptions(db_session)
    await db_session.execute(delete(Schedule).where(Schedule.id == schedule["id"]))
    await db_session.commit()

    young = await _reconciler().run()
    assert young.orphans == 0, "a create still in flight is given time"

    later = datetime.now(timezone.utc) + timedelta(minutes=30)
    done = await _reconciler(clock=lambda: later).run()

    assert done.orphans == 1
    assert await _subscriptions(db_session) == []
    assert row.remote_id in tracker.unsubscribed


async def test_an_author_who_left_the_pod_stops_their_account_listening(
    authenticated_client, fixed_test_org, db_session, tracker, tracker_account
):
    _, account = tracker_account
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    schedule = await _schedule(
        authenticated_client, pod_id, str(account.id), project="web"
    )
    [row] = await _subscriptions(db_session)

    async def gone(*_: object) -> bool:
        return False

    done = await _reconciler(is_member=gone).run()

    assert done.turned_off == 1
    off = await _schedule_row(db_session, schedule["id"])
    assert off.is_active is False
    assert "provider_trigger_id" not in off.config
    assert await _subscriptions(db_session) == []
    assert row.remote_id in tracker.unsubscribed

    # Turned back on by its author, it listens afresh on their account.
    on = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}", json={"is_active": True}
    )
    assert on.status_code == 200, on.text
    [fresh] = await _subscriptions(db_session)
    assert on.json()["config"]["provider_trigger_id"] == str(fresh.id)
    assert fresh.id != row.id
    assert len(tracker.subscriptions) == 1


async def test_a_subscription_the_server_stopped_renewing_turns_the_schedule_off(
    authenticated_client, fixed_test_org, db_session, tracker, tracker_account
):
    from sqlalchemy import update

    from app.modules.schedule.services.mcp_listening_reconciler import (
        LAPSED_AFTER_FAILURES,
    )

    _, account = tracker_account
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    schedule = await _schedule(
        authenticated_client, pod_id, str(account.id), project="web"
    )
    [row] = await _subscriptions(db_session)

    await db_session.execute(
        update(ConnectorEventSubscription)
        .where(ConnectorEventSubscription.id == row.id)
        .values(
            refresh_before=datetime.now(timezone.utc) - timedelta(hours=1),
            renew_failures=LAPSED_AFTER_FAILURES - 1,
            last_error="-32012: account needs signing in again",
        )
    )
    await db_session.commit()
    assert (await _reconciler().run()).turned_off == 0, "a short outage is retried"
    shown = (
        await authenticated_client.get(f"/pods/{pod_id}/schedules/{schedule['id']}")
    ).json()
    assert shown["listening"]["state"] == "lapsed", "and the schedule says so"
    assert "signing in again" in shown["listening"]["last_error"]

    await db_session.execute(
        update(ConnectorEventSubscription)
        .where(ConnectorEventSubscription.id == row.id)
        .values(renew_failures=LAPSED_AFTER_FAILURES)
    )
    await db_session.commit()
    assert (await _reconciler().run()).turned_off == 1

    off = await _schedule_row(db_session, schedule["id"])
    assert off.is_active is False
    assert await _subscriptions(db_session) == []
    shown = (
        await authenticated_client.get(f"/pods/{pod_id}/schedules/{schedule['id']}")
    ).json()
    assert shown["last_error"].startswith("Turned off: the server stopped accepting")
    assert shown["listening"] is None
