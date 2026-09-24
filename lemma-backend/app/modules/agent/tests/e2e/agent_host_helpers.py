"""Shared setup for the Agent Host end-to-end suites.

A paired machine is the starting point for anything that touches dispatch, and
building one means going through the real pairing exchange: a code minted by a
signed-in user, consumed by a machine that has no session and never will. Both
suites need it, and the lease rows are keyed on the host and harness it
produces, so inventing ids instead would only exercise the foreign keys.

The machine's half goes over the real link WebSocket, in process: ``HostLink``
drives ``/agent-host/link`` through the whole ASGI app -- middleware, the global
auth gate, the router -- the same way the datastore changes socket is tested.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from asgiref.testing import ApplicationCommunicator
from fastapi import status

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostRunState,
)
from app.modules.agent.domain.agent_host_link import AGENT_HOST_LINK_PATH
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel


def hello(installation_id: str | None = None) -> dict:
    """One machine is one ``installation_id``.

    Re-pairing the same installation supersedes its previous credential, so a
    test that wants two live machines must use two ids.
    """
    return {
        "installation_id": installation_id or f"e2e-{uuid4()}",
        "host_release": "0.1.0",
        "protocol_version": AGENT_HOST_PROTOCOL_VERSION,
    }


def stale_after() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def app_of(client):
    """The ASGI app behind an in-process ``AsyncClient``.

    The e2e clients are built on ``ASGITransport(app=test_app)``; a WebSocket
    has to reach the same app instance, not a second one.
    """
    return client._transport.app


class HostLink:
    """One host's link WebSocket, driven frame by frame from a test.

    A reader task takes every frame the server sends: an answer (``re``) goes to
    whoever is awaiting that request, and a push goes to ``next_push``. So many
    requests can be in flight at once, exactly as the host's MCP bridge keeps
    several tool calls in flight on one socket.
    """

    def __init__(self, app, *, secret: str | None) -> None:
        headers = [(b"host", b"testserver")]
        if secret is not None:
            headers.append((b"authorization", f"Bearer {secret}".encode()))
        self._communicator = ApplicationCommunicator(
            app,
            {
                "type": "websocket",
                "path": AGENT_HOST_LINK_PATH,
                "raw_path": AGENT_HOST_LINK_PATH.encode(),
                "query_string": b"",
                "headers": headers,
                "scheme": "ws",
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
        )
        self._next_id = 0
        self._waiting: dict[str, asyncio.Future[dict]] = {}
        self._pushes: asyncio.Queue[dict] = asyncio.Queue()
        self._closed = asyncio.Event()
        self._reader: asyncio.Task[None] | None = None
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def open(self) -> HostLink:
        await self._communicator.send_input({"type": "websocket.connect"})
        accepted = await self._communicator.receive_output(timeout=10)
        assert accepted["type"] == "websocket.accept", accepted
        self._reader = asyncio.ensure_future(self._read())
        return self

    async def _read(self) -> None:
        try:
            while True:
                message = await self._communicator.output_queue.get()
                if message["type"] == "websocket.close":
                    self.close_code = message.get("code", 1000)
                    self.close_reason = message.get("reason")
                    return
                frame = json.loads(message["text"])
                waiter = self._waiting.pop(frame.get("re") or "", None)
                if waiter is not None and not waiter.done():
                    waiter.set_result(frame)
                else:
                    self._pushes.put_nowait(frame)
        finally:
            self._closed.set()
            for waiter in self._waiting.values():
                if not waiter.done():
                    waiter.set_exception(
                        AssertionError(
                            f"the link closed ({self.close_code} "
                            f"{self.close_reason}) before answering"
                        )
                    )

    def send(self, frame_type: str, body: dict | None = None) -> str:
        self._next_id += 1
        frame_id = str(self._next_id)
        self._waiting[frame_id] = asyncio.get_running_loop().create_future()
        self._communicator.input_queue.put_nowait(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"type": frame_type, "id": frame_id, "body": body or {}}
                ),
            }
        )
        return frame_id

    async def answer_to(self, frame_id: str, timeout: float = 30) -> dict:
        return await asyncio.wait_for(self._waiting[frame_id], timeout=timeout)

    async def request(
        self, frame_type: str, body: dict | None = None, timeout: float = 30
    ) -> dict:
        return await self.answer_to(self.send(frame_type, body), timeout)

    async def next_push(self, timeout: float = 30) -> dict:
        return await asyncio.wait_for(self._pushes.get(), timeout=timeout)

    async def closed(self, timeout: float = 30) -> int:
        """Wait for the server to close the socket, and return the code."""
        await asyncio.wait_for(self._closed.wait(), timeout=timeout)
        assert self.close_code is not None, "the reader ended without a close"
        return self.close_code

    async def hello(self, machine: dict, *, capacity: dict | None = None) -> dict:
        return await self.request(
            "hello",
            {
                "hello": machine,
                "capacity": capacity
                or {"max_runs": 1, "active_runs": 0, "available_runs": 1},
            },
        )

    async def aclose(self) -> None:
        if self.close_code is None:
            await self._communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
        try:
            await asyncio.wait_for(self._communicator.wait(), timeout=10)
        except TimeoutError:
            self._communicator.future.cancel()
        if self._reader is not None:
            self._reader.cancel()


class LinkMcpClient:
    """The host's MCP bridge, reduced to the two calls it relays.

    Answers come back as the MCP SDK's own result types, so a test reads them
    exactly as it read what an MCP client session returned.
    """

    def __init__(
        self,
        link: HostLink,
        *,
        conversation_id: UUID | str,
        token: str,
        run_id: UUID | str | None = None,
    ) -> None:
        self._link = link
        self._scope = {
            "conversation_id": str(conversation_id),
            "token": token,
            "run_id": str(run_id) if run_id is not None else None,
        }

    async def _relay(self, method: str, params: dict) -> dict:
        answer = await self._link.request(
            "mcp", {**self._scope, "method": method, "params": params}, timeout=120
        )
        if answer["type"] == "error":
            raise LinkMcpError(answer["body"])
        assert answer["type"] == "mcp_ok", answer
        return answer["body"]["result"]

    async def list_tools(self):
        import mcp.types

        return mcp.types.ListToolsResult.model_validate(
            await self._relay("tools/list", {})
        )

    async def call_tool(self, name: str, arguments: dict | None = None):
        import mcp.types

        return mcp.types.CallToolResult.model_validate(
            await self._relay(
                "tools/call", {"name": name, "arguments": arguments or {}}
            )
        )


class LinkMcpError(AssertionError):
    """An ``error`` frame in answer to an ``mcp`` request."""

    def __init__(self, body: dict) -> None:
        super().__init__(f"{body.get('code')}: {body.get('message')}")
        self.code = body.get("code")


async def connected_host(
    app, machine: dict, *, capacity: dict | None = None
) -> HostLink:
    """A link that has said ``hello`` and been welcomed."""
    link = await HostLink(app, secret=machine["host_secret"]).open()
    welcome = await link.hello(machine["hello"], capacity=capacity)
    assert welcome["type"] == "welcome", welcome
    return link


async def pair(
    authenticated_client,
    async_client,
    *,
    display_name: str,
    machine: dict | None = None,
) -> dict:
    """Mint a code as the user, then consume it on the link as the machine would."""
    machine = machine or hello()
    minted = await authenticated_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": display_name, "organization_id": None},
    )
    assert minted.status_code == status.HTTP_200_OK, minted.text

    link = await HostLink(app_of(async_client), secret=None).open()
    try:
        paired = await link.request(
            "pair",
            {
                "pairing_code": minted.json()["pairing_code"],
                "display_name": display_name,
                "hello": machine,
            },
        )
        assert paired["type"] == "paired", paired
        assert await link.closed() == 1000
    finally:
        await link.aclose()
    return {**paired["body"], "hello": machine}


async def publish_harnesses(async_client, machine: dict, harnesses: list[dict]) -> dict:
    """Publish over a short-lived link, as a host does when its agents change."""
    link = await connected_host(app_of(async_client), machine)
    try:
        return await link.request("harnesses", {"harnesses": harnesses})
    finally:
        await link.aclose()


async def paired_machine(
    scenario,
    *,
    display_name: str = "e2e machine",
    harness_key: str = "codex",
    load_session: bool = True,
    capabilities: dict | None = None,
    config_options: list | None = None,
) -> dict:
    """A paired machine with one published harness, and its credential.

    Returns the pairing response plus ``host_id`` and ``harness_id`` as UUIDs,
    so callers can build lease rows against real foreign keys.
    """
    paired = await pair(
        scenario.owner_client, scenario.async_client, display_name=display_name
    )
    published = await publish_harnesses(
        scenario.async_client,
        paired,
        [
            {
                "harness_key": harness_key,
                "display_name": harness_key.title(),
                "adapter_version": "1.0.0",
                "health": "READY",
                "capabilities": (
                    {"load_session": load_session}
                    if capabilities is None
                    else capabilities
                ),
                "config_revision": "rev-1",
                "config_options": config_options or [],
                "stale_after": stale_after(),
            }
        ],
    )
    assert published["type"] == "harnesses_ok", published
    return {
        **paired,
        "host_id": UUID(paired["host_id"]),
        "harness_id": UUID(published["body"]["items"][0]["id"]),
    }


async def conversation_with_a_leased_run(
    db_session,
    scenario,
    *,
    host_id: UUID,
    harness_id: UUID,
    state: AgentHostRunState = AgentHostRunState.ACCEPTED,
    title: str = "e2e",
) -> tuple[UUID, UUID]:
    """A conversation mid-run, as a real dispatched turn leaves it."""
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"title": title},
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])

    now = datetime.now(timezone.utc)
    run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        started_at=now,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        AgentHostRunLeaseModel(
            run_id=run.id,
            host_id=host_id,
            harness_id=harness_id,
            lease_epoch=1,
            state=state.value,
            # Accepted is the fence past which a run is never repeated, so a
            # test standing in for a dispatched run has to have crossed it.
            accepted_at=now if state is not AgentHostRunState.QUEUED_FOR_HOST else None,
            lease_expires_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.flush()
    return conversation_id, run.id
