"""Stand-ins for everything an Agent Host link session talks to.

The session's collaborators are all injected -- the socket, the store, the MCP
relay, the channel service, the registry -- so these replace each one at its
seam rather than patching anything inside the session. The store fake keeps the
rules a test asserts on (a wrong secret, a protocol mismatch) and nothing else;
the rules themselves are the repositories', and are tested there.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from mcp.types import CallToolResult, TextContent, Tool

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostCapacity,
    AgentHostCommand,
    AgentHostCommandKind,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostHarnessSnapshot,
    AgentHostPairingCompleted,
    AgentHostStatus,
    HostHello,
)
from app.modules.agent.domain.agent_host_link import AgentHostHarnessRecord
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostPairingRejected,
    AgentHostRepositoryError,
)
from app.modules.agent.services.agent_host_link_mcp import AgentHostLinkMcp
from app.modules.agent.services.agent_host_link_registry import (
    AgentHostLinkRegistry,
)
from app.modules.agent.services.agent_host_link_session import AgentHostLinkSession
from app.modules.agent.services.agent_host_link_store import (
    ControlUpdates,
    LinkedHost,
)


SECRET = "host-secret"
PAIRING_CODE = "pairing-code-0123456789"
TOKEN = "run-token"


def hello(protocol_version: int = AGENT_HOST_PROTOCOL_VERSION) -> dict:
    return {
        "installation_id": "install-1",
        "host_release": "0.9.0",
        "protocol_version": protocol_version,
    }


def command(
    kind: AgentHostCommandKind = AgentHostCommandKind.CANCEL_RUN,
) -> AgentHostCommand:
    now = datetime.now(timezone.utc)
    return AgentHostCommand(
        command_id=uuid7(),
        kind=kind,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        run_id=uuid7(),
        lease_epoch=1,
    )


class FakeSocket:
    """An accepted WebSocket driven from the test, one ASGI message at a time."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = (
            headers if headers is not None else {"authorization": f"Bearer {SECRET}"}
        )
        self._inbox: asyncio.Queue[dict] = asyncio.Queue()
        self._outbox: asyncio.Queue[dict] = asyncio.Queue()
        self.accepted = False
        self.closed: tuple[int, str | None] | None = None
        self._next_id = 0

    # the socket, as the session sees it
    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        return await self._inbox.get()

    async def send_text(self, data: str) -> None:
        if self.closed is not None:
            raise RuntimeError("socket is closed")
        await self._outbox.put(json.loads(data))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)

    # the host, as the test drives it
    def send(
        self, frame_type: str, body: dict | None = None, *, request: bool = True
    ) -> str | None:
        frame: dict = {"type": frame_type, "body": body or {}}
        frame_id = None
        if request:
            self._next_id += 1
            frame_id = str(self._next_id)
            frame["id"] = frame_id
        self._inbox.put_nowait({"type": "websocket.receive", "text": json.dumps(frame)})
        return frame_id

    def send_raw(self, message: dict) -> None:
        self._inbox.put_nowait(message)

    def hang_up(self) -> None:
        self._inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})

    async def frame(self, timeout: float = 2.0) -> dict:
        return await asyncio.wait_for(self._outbox.get(), timeout=timeout)

    async def answer_to(self, frame_id: str | None, timeout: float = 2.0) -> dict:
        """The answer to ``frame_id``, skipping pushes that arrive first."""
        while True:
            frame = await self.frame(timeout)
            if frame.get("re") == frame_id:
                return frame

    def nothing_sent(self) -> bool:
        return self._outbox.empty()


class FakeStore:
    """The database half, as a dictionary of what it was told."""

    def __init__(self) -> None:
        self.host_id = uuid7()
        self.user_id = uuid7()
        self.revoked = False
        self.queue: list[AgentHostCommand] = []
        self.applied: list[ControlUpdates] = []
        self.appended: list[AgentHostEventBatch] = []
        self.published: list[AgentHostHarnessSnapshot] = []
        self.append_error: Exception | None = None
        self.control_error: Exception | None = None
        self.reads = 0

    async def consume_pairing_code(self, body) -> AgentHostPairingCompleted:
        if body.pairing_code != PAIRING_CODE:
            raise AgentHostPairingRejected("pairing code is invalid or expired")
        return AgentHostPairingCompleted(
            host_id=self.host_id, user_id=self.user_id, host_secret=SECRET
        )

    async def open_link(
        self, *, secret: str, hello: HostHello, capacity: AgentHostCapacity
    ) -> LinkedHost | None:
        if secret != SECRET or self.revoked:
            return None
        status = (
            AgentHostStatus.ONLINE
            if hello.protocol_version == AGENT_HOST_PROTOCOL_VERSION
            else AgentHostStatus.UPGRADE_REQUIRED
        )
        return LinkedHost(host_id=self.host_id, user_id=self.user_id, status=status)

    async def apply_control(
        self, *, host_id: UUID, hello: HostHello, updates: ControlUpdates
    ) -> list[AgentHostCommand]:
        if self.control_error is not None:
            raise self.control_error
        self.applied.append(updates)
        acknowledged = set(updates.acknowledged_command_ids)
        self.queue = [c for c in self.queue if c.command_id not in acknowledged]
        return list(self.queue)

    async def read_commands(
        self, *, host_id: UUID, available_run_slots: int
    ) -> list[AgentHostCommand]:
        self.reads += 1
        return list(self.queue)

    async def append_events(
        self, *, host_id: UUID, batch: AgentHostEventBatch
    ) -> AgentHostEventAck:
        if self.append_error is not None:
            raise self.append_error
        self.appended.append(batch)
        last = batch.events[-1]
        return AgentHostEventAck(
            run_id=last.run_id,
            lease_epoch=last.lease_epoch,
            acked_through=last.sequence,
        )

    async def publish_harnesses(
        self, *, host_id: UUID, snapshots: list[AgentHostHarnessSnapshot]
    ) -> list[AgentHostHarnessRecord]:
        self.published.extend(snapshots)
        return [
            AgentHostHarnessRecord(
                id=uuid7(),
                host_id=host_id,
                harness_key=snapshot.harness_key,
                display_name=snapshot.display_name,
                adapter_version=snapshot.adapter_version,
                upstream_version=snapshot.upstream_version,
                health=snapshot.health.value,
                capabilities=snapshot.capabilities.model_dump(mode="json"),
                config_revision=snapshot.config_revision,
                config_options=[],
                stale_after=snapshot.stale_after,
                stale_reason=snapshot.stale_reason,
            )
            for snapshot in snapshots
        ]

    async def revoke_host(self, *, host_id: UUID, user_id: UUID) -> None:
        if self.revoked:
            raise AgentHostRepositoryError("already revoked")
        self.revoked = True


class FakeChannels:
    """In-memory pub/sub with the channel service's shape."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self.published: list[tuple[str, object]] = []

    async def publish(self, channel: str, message: object) -> None:
        self.published.append((channel, message))
        payload = (
            message if isinstance(message, str) else json.dumps(message, default=str)
        )
        for queue in list(self._subscribers[channel]):
            queue.put_nowait(payload)

    @asynccontextmanager
    async def subscribe(
        self, channels: Sequence[str]
    ) -> AsyncIterator[AsyncIterator[str]]:
        queue: asyncio.Queue = asyncio.Queue()
        for channel in channels:
            self._subscribers[channel].append(queue)

        async def messages() -> AsyncIterator[str]:
            while True:
                yield await queue.get()

        try:
            yield messages()
        finally:
            for channel in channels:
                self._subscribers[channel].remove(queue)

    def subscribers(self, channel: str) -> int:
        return len(self._subscribers[channel])


class FakeConversationMcp:
    """The conversation MCP service: one conversation, one token."""

    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        self.answers: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.release_call = asyncio.Event()
        self.release_call.set()
        self.decided_checks = 0

    async def authorize(self, *, conversation_id: UUID, token: str) -> bool:
        return conversation_id == self.conversation_id and token == TOKEN

    async def list_tools(
        self, *, conversation_id: UUID, agent_run_id: UUID | None = None
    ) -> list[Tool]:
        return [
            Tool(
                name="lemma_pod_get_records",
                description="Read records",
                input_schema={"type": "object"},
            )
        ]

    async def call_tool(
        self,
        *,
        conversation_id: UUID,
        name: str,
        arguments: dict | None,
        agent_run_id: UUID | None = None,
    ) -> CallToolResult:
        self.calls.append((name, arguments or {}))
        await self.release_call.wait()
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structured_content={"success": True},
        )

    async def parked_tool_return(
        self, *, conversation_id: UUID, tool_call_id: str
    ) -> dict | None:
        self.decided_checks += 1
        return self.answers.get(tool_call_id)


class Link:
    """One session under test and everything wired to it."""

    def __init__(
        self,
        *,
        store: FakeStore | None = None,
        channels: FakeChannels | None = None,
        registry: AgentHostLinkRegistry | None = None,
        socket: FakeSocket | None = None,
        conversation_id: UUID | None = None,
        heartbeat_ms: int = 20_000,
        push_floor_seconds: float = 60.0,
        resend_after_seconds: float = 30.0,
        recheck_seconds: float = 60.0,
        max_in_flight: int | None = None,
    ) -> None:
        self.store = store or FakeStore()
        self.channels = channels or FakeChannels()
        self.registry = registry if registry is not None else AgentHostLinkRegistry()
        self.socket = socket or FakeSocket()
        self.conversation_id = conversation_id or uuid7()
        self.mcp_service = FakeConversationMcp(self.conversation_id)
        self.session = AgentHostLinkSession(
            self.socket,
            store=self.store,
            mcp=AgentHostLinkMcp(
                self.mcp_service, self.channels, recheck_seconds=recheck_seconds
            ),
            channels=self.channels,
            registry=self.registry,
            heartbeat_ms=heartbeat_ms,
            push_floor_seconds=push_floor_seconds,
            resend_after_seconds=resend_after_seconds,
            max_in_flight=max_in_flight,
        )
        self.task: asyncio.Task | None = None

    def start(self) -> Link:
        self.task = asyncio.ensure_future(self.session.serve_link())
        return self

    async def open(self, **capacity) -> dict:
        """Say hello and return the welcome."""
        self.start()
        frame_id = self.socket.send(
            "hello",
            {
                "hello": hello(),
                "capacity": capacity or {"max_runs": 2, "available_runs": 2},
            },
        )
        welcome = await self.socket.answer_to(frame_id)
        assert welcome["type"] == "welcome", welcome
        return welcome

    async def ended(self, timeout: float = 2.0) -> tuple[int, str | None] | None:
        assert self.task is not None
        await asyncio.wait_for(self.task, timeout=timeout)
        return self.socket.closed

    async def close(self) -> None:
        if self.task is not None and not self.task.done():
            self.socket.hang_up()
            await asyncio.wait_for(self.task, timeout=2.0)
