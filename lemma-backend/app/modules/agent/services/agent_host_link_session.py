"""One Agent Host link: a WebSocket from a paired machine, for as long as it lives.

See docs/architecture/agent-host.md#the-link for the protocol; this is the
server's half of it. Three things run per connection:

* the **reader** takes frames off the socket in order. ``control``, ``events``,
  ``harnesses`` and ``revoke`` are answered before the next frame is read,
  because their order is their meaning -- two event batches for one run must
  land in the order they were sent. ``mcp`` and ``interaction_wait`` are handed
  to their own tasks instead: a tool call can take minutes and a parked
  interaction can take half an hour, and neither may stop the heartbeat that
  follows it from being read. Each kind has its own slots; a named
  ``tools/call`` outlives the socket (``agent_host_link_tool_calls``).
* the **pusher** listens on the host's notice channel and pushes commands on
  every poke and every 5 seconds, and ends the connection when it hears it was
  superseded or revoked: see ``agent_host_link_pusher``.
* the **writer** is a lock, not a task: every frame goes out whole, from
  whichever task produced it, and never interleaved with another.

No database connection is held by any of them between frames: each frame's
work is one short transaction in ``AgentHostLinkStore``.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID, uuid7

from pydantic import ValidationError

from app.core.domain.realtime import RealtimeChannel
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostCapacity,
    AgentHostEventBatch,
    AgentHostStatus,
    HostHello,
)
from app.modules.agent.domain.agent_host_link import (
    AGENT_HOST_LINK_HEARTBEAT_MS,
    AGENT_HOST_LINK_MISSED_HEARTBEATS,
    REVOKED_OR_MISSING_REASON,
    CommandsBody,
    ControlBody,
    ControlOkBody,
    EventsOkBody,
    HarnessesBody,
    HarnessesOkBody,
    HelloBody,
    HostFrameType,
    InteractionOkBody,
    InteractionWaitBody,
    LinkCloseCode,
    LinkErrorCode,
    LinkFrame,
    McpBody,
    McpOkBody,
    PairBody,
    ReconnectBody,
    ServerFrameType,
    WelcomeBody,
)
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostNotFound,
    AgentHostProtocolViolation,
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.agent_host.transfers import (
    AgentHostTransferStore,
)
from app.modules.agent.services.agent_host_link_ops import LinkOpRelay
from app.modules.agent.services.agent_host_link_outbound import (
    OutboundStager,
    command_frames,
)
from app.modules.agent.services.agent_host_link_mcp import AgentHostLinkMcp
from app.modules.agent.services.conversation_mcp_authorization import RelayingHost
from app.modules.agent.services.agent_host_link_ownership import LinkOwnership
from app.modules.agent.services.agent_host_link_pusher import LinkPusher
from app.modules.agent.services.agent_host_link_store import (
    AgentHostLinkStore,
    LinkedHost,
)
from app.modules.agent.services.agent_host_link_wire import (
    INTERNAL_ERROR_CLOSE,
    answer_request,
    EmptyBody,
    LinkClose,
    LinkRequestError,
    LinkSocket,
    LinkWriter,
    OutboundFrameTooLarge,
    SentCommands,
    TRANSIENT_ERRORS,
    bearer_secret,
    event_error,
    log_host_reported_error,
    parse_control_items,
    validation_summary,
)


logger = get_logger(__name__)


#: The floor under the poke: how long a lost poke can delay a command.
PUSH_FLOOR_SECONDS = 5.0
#: A command still unacknowledged is pushed again at most this often on one
#: socket. The host de-duplicates by ``command_id`` anyway; this only stops the
#: 5-second floor re-sending the same START_RUN twelve times a minute.
RESEND_AFTER_SECONDS = 30.0
#: Tool calls one socket may have in flight at once. A host runs a handful of
#: agents; past this it is a runaway, not a workload.
MAX_IN_FLIGHT_REQUESTS = 64
#: Parked ``interaction_wait``s, apart from tool calls: a wait holds its slot
#: as long as a person takes, and must never starve the runs still working.
MAX_PARKED_WAITS = 64
#: The spread a draining replica gives its hosts before they reconnect, so a
#: deploy does not bring every host back in the same instant.
RECONNECT_SPREAD_MS = 5_000


class LinkRegistry(Protocol):
    def add(self, session: AgentHostLinkSession) -> None: ...

    def discard(self, session: AgentHostLinkSession) -> None: ...


class AgentHostLinkSession:
    def __init__(
        self,
        socket: LinkSocket,
        *,
        store: AgentHostLinkStore,
        mcp: AgentHostLinkMcp,
        channels: RealtimeChannel,
        registry: LinkRegistry,
        heartbeat_ms: int = AGENT_HOST_LINK_HEARTBEAT_MS,
        push_floor_seconds: float | None = None,
        resend_after_seconds: float | None = None,
        max_in_flight: int | None = None,
        max_waits: int | None = None,
        transfers: AgentHostTransferStore | None = None,
    ) -> None:
        # The tunables are read here rather than bound as defaults, so a test
        # that shortens the module's floor reaches the session the route builds.
        self.connection_id = uuid7()
        self._socket = socket
        self._store = store
        self._mcp = mcp
        self._channels = channels
        self._registry = registry
        self._heartbeat_ms = heartbeat_ms
        self._silence_limit = heartbeat_ms * AGENT_HOST_LINK_MISSED_HEARTBEATS / 1000
        self._push_floor_seconds = (
            PUSH_FLOOR_SECONDS if push_floor_seconds is None else push_floor_seconds
        )
        self._in_flight = asyncio.Semaphore(
            MAX_IN_FLIGHT_REQUESTS if max_in_flight is None else max_in_flight
        )
        self._waits = asyncio.Semaphore(
            MAX_PARKED_WAITS if max_waits is None else max_waits
        )
        self._stopped = asyncio.Event()
        self._writer = LinkWriter(socket, on_lost=self._stopped.set)
        self._transfers = transfers or AgentHostTransferStore()
        self.ops = LinkOpRelay(
            writer=self._writer, channels=channels, transfers=self._transfers
        )
        self._finished = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._close_code: int | None = None
        self._close_reason = ""
        self._host: LinkedHost | None = None
        self._ownership = LinkOwnership(
            store=store, channels=channels, connection_id=self.connection_id
        )
        self._hello: HostHello | None = None
        #: What is too large for this host's link is staged by this; set by
        #: ``hello``, which says whether the host can fetch it.
        self._stager: OutboundStager | None = None
        self._capacity = AgentHostCapacity()
        self._sent = SentCommands(
            RESEND_AFTER_SECONDS
            if resend_after_seconds is None
            else resend_after_seconds
        )

    @property
    def host_id(self) -> UUID | None:
        return self._host.host_id if self._host is not None else None

    # ------------------------------------------------------------- lifecycle

    async def serve_link(self) -> None:
        """Serve the socket until either side ends it."""
        await self._socket.accept()
        serving = asyncio.ensure_future(self._serve())
        stopping = asyncio.ensure_future(self._stopped.wait())
        try:
            await asyncio.wait({serving, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if serving.done() and not serving.cancelled() and serving.exception():
                self._fail(serving.exception(), "reader")
        finally:
            pending = [serving, stopping, *self._tasks]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await self.ops.close()
            await self._hang_up()
            self._registry.discard(self)
            self._finished.set()
            logger.info(
                "agent.agent_host_link.disconnected",
                host_id=str(self.host_id) if self.host_id else None,
                connection_id=str(self.connection_id),
                close_code=self._close_code,
                close_reason=self._close_reason or None,
                peer_closed=self._writer.peer_gone,
            )

    def stop(self, code: int, reason: str = "") -> None:
        """Ask the connection to close; the first reason given is the one sent."""
        if self._close_code is None:
            self._close_code, self._close_reason = code, reason
        self._stopped.set()

    async def drain_link(self) -> None:
        """This replica is going away: tell the host when to come back, then close."""
        if self._stopped.is_set():
            return
        await self._writer.send_frame(
            ServerFrameType.RECONNECT,
            ReconnectBody(after_ms=random.randint(0, RECONNECT_SPREAD_MS)),
        )
        self.stop(LinkCloseCode.RESTARTING, "restarting")

    async def finished(self) -> None:
        await self._finished.wait()

    async def _hang_up(self) -> None:
        if self._close_code is not None:
            await self._writer.close_socket(self._close_code, self._close_reason)

    def _fail(self, exc: BaseException | None, where: str) -> None:
        logger.error(
            "agent.agent_host_link.task.failed",
            host_id=str(self.host_id) if self.host_id else None,
            connection_id=str(self.connection_id),
            link_task=where,
            exc_info=exc,
        )
        self.stop(INTERNAL_ERROR_CLOSE, "internal error")

    # --------------------------------------------------------------- reading

    async def _serve(self) -> None:
        try:
            first = await self._next_frame()
            if first is None:
                return
            if first.type == HostFrameType.PAIR:
                await self._pair(first)
                return
            if first.type != HostFrameType.HELLO:
                raise LinkClose(
                    LinkCloseCode.PROTOCOL_VIOLATION,
                    "the first frame must be pair or hello",
                )
            await self._open(first)
            while (frame := await self._next_frame()) is not None:
                await self._dispatch(frame)
        except LinkClose as close:
            self.stop(close.code, close.reason)

    async def _next_frame(self) -> LinkFrame | None:
        """The next frame, or ``None`` once the host has gone.

        Silence past three heartbeats closes the link: a host that stopped
        talking is either gone without a close or wedged, and either way its
        leases are about to lapse.
        """
        try:
            message = await asyncio.wait_for(
                self._socket.receive(), timeout=self._silence_limit
            )
        except TimeoutError:
            raise LinkClose(
                LinkCloseCode.HEARTBEAT_TIMEOUT, "no frame from the host"
            ) from None
        if message.get("type") == "websocket.disconnect":
            self._writer.lost()
            return None
        text = message.get("text")
        if not isinstance(text, str):
            raise LinkClose(LinkCloseCode.PROTOCOL_VIOLATION, "frames are JSON text")
        try:
            return LinkFrame.model_validate_json(text)
        except ValidationError:
            raise LinkClose(
                LinkCloseCode.PROTOCOL_VIOLATION, "a frame is not a valid envelope"
            ) from None

    async def _dispatch(self, frame: LinkFrame) -> None:
        in_order: dict[str, Callable[[LinkFrame], Awaitable[None]]] = {
            HostFrameType.CONTROL: self._control,
            HostFrameType.EVENTS: self._events,
            HostFrameType.HARNESSES: self._harnesses,
            HostFrameType.REVOKE: self._revoke,
        }
        concurrent: dict[
            str, tuple[Callable[[LinkFrame], Awaitable[None]], asyncio.Semaphore]
        ] = {
            HostFrameType.MCP: (self._mcp_request, self._in_flight),
            HostFrameType.INTERACTION_WAIT: (self._interaction_wait, self._waits),
        }
        if frame.type in in_order:
            await self._supervised(frame, in_order[frame.type])
        elif frame.type in concurrent and concurrent[frame.type][1].locked():
            # Refused rather than queued: waiting for a slot here would stop
            # the reader, and with it the heartbeat that renews every lease.
            await self._writer.send_error(
                frame,
                LinkErrorCode.UNAVAILABLE,
                "too many requests in flight on this link",
                retryable=True,
            )
        elif frame.type in concurrent:
            handler, slots = concurrent[frame.type]
            await slots.acquire()
            self._spawn(frame, handler, slots)
        elif frame.type == HostFrameType.OP_OK:
            self.ops.answer_ok(frame)
        elif frame.type == HostFrameType.ERROR:
            if not self.ops.answer_error(frame):
                log_host_reported_error(frame, host_id=self.host_id)
        elif frame.type in {HostFrameType.PAIR, HostFrameType.HELLO}:
            raise LinkClose(
                LinkCloseCode.PROTOCOL_VIOLATION, f"{frame.type} after hello"
            )
        else:
            await self._writer.send_error(
                frame, LinkErrorCode.INVALID_FRAME, f"unknown frame type {frame.type!r}"
            )

    def _spawn(
        self,
        frame: LinkFrame,
        handler: Callable[[LinkFrame], Awaitable[None]],
        slots: asyncio.Semaphore,
    ) -> None:
        async def answer() -> None:
            try:
                await self._supervised(frame, handler)
            finally:
                slots.release()

        task = asyncio.ensure_future(answer())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _supervised(
        self, frame: LinkFrame, handler: Callable[[LinkFrame], Awaitable[None]]
    ) -> None:
        await answer_request(
            frame,
            handler,
            writer=self._writer,
            stop=self.stop,
            host_id=self.host_id,
            connection_id=self.connection_id,
        )

    # --------------------------------------------------- the opening frame

    async def _pair(self, frame: LinkFrame) -> None:
        try:
            body = PairBody.model_validate(frame.body)
            paired = await self._store.consume_pairing_code(body)
        except ValidationError as exc:
            await self._writer.send_error(
                frame, LinkErrorCode.INVALID_FRAME, validation_summary(exc)
            )
            raise LinkClose(
                LinkCloseCode.PROTOCOL_VIOLATION, "invalid pair frame"
            ) from None
        except AgentHostRepositoryError as exc:
            # The pairing code is this socket's credential, and it was refused.
            await self._writer.send_error(frame, LinkErrorCode.UNAUTHORIZED, str(exc))
            raise LinkClose(LinkCloseCode.INVALID_CREDENTIAL, exc.code) from None
        await self._writer.send_frame(ServerFrameType.PAIRED, paired, re=frame.id)
        raise LinkClose(LinkCloseCode.NORMAL, "paired")

    async def _open(self, frame: LinkFrame) -> None:
        secret = bearer_secret(self._socket.headers)
        if secret is None:
            raise LinkClose(
                LinkCloseCode.INVALID_CREDENTIAL, "missing or malformed credential"
            )
        try:
            body = HelloBody.model_validate(frame.body)
        except ValidationError:
            raise LinkClose(
                LinkCloseCode.PROTOCOL_VIOLATION, "invalid hello frame"
            ) from None
        try:
            host = await self._store.open_link(
                secret=secret,
                hello=body.hello,
                capacity=body.capacity,
                host_execution=body.host_execution,
            )
        except AgentHostRepositoryError:
            host = None
        if host is None:
            raise LinkClose(LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON)
        if host.status is AgentHostStatus.UPGRADE_REQUIRED:
            raise LinkClose(LinkCloseCode.UPGRADE_REQUIRED, "upgrade required")
        self._host, self._hello, self._capacity = host, body.hello, body.capacity
        self._ownership.generation = host.link_generation
        self._writer.host_id = host.host_id
        self._stager = OutboundStager(
            host_id=host.host_id,
            host_fetches_staged=body.staged_bodies,
            store=self._transfers,
        )
        self.ops.host_moves_transfers = bool(
            body.host_execution and body.host_execution.transfers
        )
        await self._writer.send_frame(
            ServerFrameType.WELCOME,
            WelcomeBody(
                host_id=host.host_id,
                user_id=host.user_id,
                heartbeat_ms=self._heartbeat_ms,
            ),
            re=frame.id,
        )
        self._registry.add(self)
        logger.info(
            "agent.agent_host_link.connected",
            host_id=str(host.host_id),
            connection_id=str(self.connection_id),
            host_release=body.hello.host_release,
        )
        pusher = asyncio.ensure_future(
            LinkPusher(
                store=self._store,
                channels=self._channels,
                writer=self._writer,
                ops=self.ops,
                ownership=self._ownership,
                sent=self._sent,
                connection_id=self.connection_id,
                push_floor_seconds=self._push_floor_seconds,
                available_runs=lambda: self._capacity.available_runs,
                stop=self.stop,
                stager=self._stager,
            ).push_loop(host.host_id)
        )
        self._tasks.add(pusher)
        pusher.add_done_callback(self._pusher_ended)

    # ----------------------------------------------------- request frames

    def _linked(self) -> tuple[LinkedHost, HostHello]:
        if self._host is None or self._hello is None:
            raise LinkClose(LinkCloseCode.PROTOCOL_VIOLATION, "no hello on this link")
        return self._host, self._hello

    async def _control(self, frame: LinkFrame) -> None:
        host, hello = self._linked()
        updates, refused = parse_control_items(ControlBody.model_validate(frame.body))
        try:
            commands = await self._store.apply_control(
                host_id=host.host_id, hello=hello, updates=updates
            )
        except AgentHostNotFound, AgentHostProtocolViolation:
            # The host was revoked, or its identity no longer matches the
            # credential, between frames.
            raise LinkClose(
                LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON
            ) from None
        self._capacity = updates.capacity
        self._sent.acknowledged(updates.acknowledged_command_ids)
        fresh = self._sent.unsent(commands)
        try:
            frames = await command_frames(self._stager, fresh, host_id=host.host_id)
        except TRANSIENT_ERRORS:
            # The heartbeat is answered regardless: commands whose payload
            # could not be staged go out on a later push.
            self._sent.forget([command.command_id for command in fresh])
            logger.warning(
                "agent.agent_host_link.push_skipped.degraded",
                host_id=str(host.host_id),
                exc_info=True,
            )
            frames = []
        await self._writer.send_frame(
            ServerFrameType.CONTROL_OK,
            ControlOkBody(commands=frames[0] if frames else [], refused=refused),
            re=frame.id,
        )
        # What did not fit beside the answer follows it, a frame at a time.
        for more in frames[1:]:
            try:
                await self._writer.send_frame(
                    ServerFrameType.COMMANDS, CommandsBody(commands=more)
                )
            except OutboundFrameTooLarge:
                continue  # Logged by the writer; see ``LinkPusher``.

    async def _events(self, frame: LinkFrame) -> None:
        host, _ = self._linked()
        batch = AgentHostEventBatch.model_validate(frame.body)
        try:
            ack = await self._store.append_events(host_id=host.host_id, batch=batch)
        except AgentHostRepositoryError as exc:
            raise event_error(exc) from None
        await self._writer.send_frame(
            ServerFrameType.EVENTS_OK, EventsOkBody(ack=ack), re=frame.id
        )

    async def _harnesses(self, frame: LinkFrame) -> None:
        host, _ = self._linked()
        body = HarnessesBody.model_validate(frame.body)
        for snapshot in body.harnesses:
            # An option the agent added that this build cannot offer. Named by
            # its type, never by its content; the rest of the harness stands.
            if snapshot.skipped_config_options:
                logger.info(
                    "agent.agent_host_link.config_options_skipped",
                    host_id=str(host.host_id),
                    harness_key=snapshot.harness_key,
                    skipped=sorted(set(snapshot.skipped_config_options)),
                )
        try:
            items = await self._store.publish_harnesses(
                host_id=host.host_id, snapshots=body.harnesses
            )
        except AgentHostRepositoryError as exc:
            raise LinkRequestError(LinkErrorCode.INVALID_FRAME, str(exc)) from None
        await self._writer.send_frame(
            ServerFrameType.HARNESSES_OK, HarnessesOkBody(items=items), re=frame.id
        )

    async def _revoke(self, frame: LinkFrame) -> None:
        host, _ = self._linked()
        try:
            await self._store.revoke_host(host_id=host.host_id, user_id=host.user_id)
        except AgentHostNotFound:
            raise LinkClose(
                LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON
            ) from None
        await self._writer.send_frame(ServerFrameType.REVOKED, EmptyBody(), re=frame.id)
        raise LinkClose(LinkCloseCode.NORMAL, "revoked")

    def _relaying_host(self) -> RelayingHost:
        host, _ = self._linked()
        return RelayingHost(host_id=host.host_id, user_id=host.user_id)

    async def _mcp_request(self, frame: LinkFrame) -> None:
        result = await self._mcp.relay_request(
            McpBody.model_validate(frame.body), host=self._relaying_host()
        )
        if self._stager is not None:
            result = await self._stager.tool_result(result)
        await self._writer.send_frame(
            ServerFrameType.MCP_OK, McpOkBody(result=result), re=frame.id
        )

    async def _interaction_wait(self, frame: LinkFrame) -> None:
        host = self._relaying_host()
        body = InteractionWaitBody.model_validate(frame.body)
        try:
            answer = await self._mcp.wait_for_interaction(body, host=host)
        except TimeoutError:
            raise LinkRequestError(
                LinkErrorCode.UNAVAILABLE,
                "the interaction was not decided in time",
                retryable=True,
            ) from None
        await self._writer.send_frame(
            ServerFrameType.INTERACTION_OK,
            InteractionOkBody(answer=answer),
            re=frame.id,
        )

    # ---------------------------------------------------------------- pushing

    def _pusher_ended(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or task.exception() is None:
            return
        self._fail(task.exception(), "pusher")
