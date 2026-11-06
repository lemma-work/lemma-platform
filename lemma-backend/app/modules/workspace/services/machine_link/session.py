"""One connected machine's link, from its first frame to its close.

See docs/architecture/connected-machines.md. The Agent Host link route accepts
the socket and reads the first frame; a frame naming ``role: "compute"`` is
handed here (``AgentHostLinkSession``'s ``compute`` handler), and everything
after is this class's. It ends by raising ``LinkClose``, which the Agent Host
session turns into the close code, exactly as its own handlers do.

Three things run while the link is open:

* the **reader** takes text frames (``control``, ``revoke``, ``error``) and
  binary ones (mux frames, fed to the ``Mux``) in order;
* the **watcher** holds the machine's notice channel, claims the link's
  generation, records the route other replicas proxy through and refreshes it
  every heartbeat, and closes the link with 4409 or 4401 when a newer link or a
  revocation says so;
* the **runtime push** sends the runtime overlay down a stream once, when the
  machine is not serving this server's bundle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid7

from pydantic import ValidationError

from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.log.log import get_logger
from app.modules.agent.contracts.compute_link import AGENT_HOST_PROTOCOL_VERSION
from app.modules.agent.contracts.compute_link import (
    AGENT_HOST_LINK_HEARTBEAT_MS,
    AGENT_HOST_LINK_MISSED_HEARTBEATS,
    REVOKED_OR_MISSING_REASON,
    HostFrameType,
    LinkCloseCode,
    LinkErrorCode,
    LinkFrame,
    ServerFrameType,
)
from app.modules.agent.contracts.compute_link import (
    COMPUTE_MIN_PROTOCOL_VERSION,
    COMPUTE_PAIRING_TTL_SECONDS,
    ComputeControlBody,
    ComputeControlOkBody,
    ComputeHelloBody,
    ComputePairBody,
    ComputePairedBody,
    ComputeWelcomeBody,
    MachineReport,
    RuntimeGrant,
)
from app.modules.agent.contracts.compute_link import (
    REVOKED,
    parse_host_notice,
)
from app.modules.agent.contracts.compute_link import notice_stream
from app.modules.agent.contracts.compute_link import LinkOwnership
from app.modules.agent.contracts.compute_link import (
    TRANSIENT_ERRORS,
    EmptyBody,
    LinkClose,
    LinkSocket,
    LinkWriter,
    bearer_secret,
    log_host_reported_error,
    validation_summary,
)
from app.modules.workspace.domain.machine_connection import (
    MachineConnection,
    MachineConnectionError,
    normalize_pairing_code,
    secret_hash,
)
from app.modules.workspace.services.machine_link.routes import (
    LinkRoutes,
    notice_channel,
)
from app.modules.workspace.services.machine_link.runtime_push import push_runtime
from app.modules.workspace.services.machine_link.store import MachineLinkStore
from sandbox_runtime.machine_link.mux import Mux, MuxError, MuxStream

logger = get_logger(__name__)

#: How often a waiting ``pair`` looks for the person's approval when no notice
#: arrives. The notice is the fast path; this is the floor under it.
PAIR_RECHECK_SECONDS = 2.0


def pairing_channel(code_hash: str) -> str:
    """Where approving a code is announced, so the waiting ``pair`` answers."""
    return f"machine-link:pairing:{code_hash}"


class MachineLinkRegistry:
    """The compute links this process holds, by connection.

    Process-local: other replicas learn where a link is from its route in
    Redis. If two links of one machine are briefly both here (a reconnect
    before the old socket noticed), the newer generation is the one served.
    """

    def __init__(self) -> None:
        self._sessions: dict[UUID, ComputeLinkSession] = {}

    def add(self, session: ComputeLinkSession) -> None:
        current = self._sessions.get(session.connection_id)
        if current is None or current.generation <= session.generation:
            self._sessions[session.connection_id] = session

    def discard(self, session: ComputeLinkSession) -> None:
        if self._sessions.get(session.connection_id) is session:
            del self._sessions[session.connection_id]

    def get(self, connection_id: UUID) -> ComputeLinkSession | None:
        return self._sessions.get(connection_id)

    def __len__(self) -> int:
        return len(self._sessions)


class ComputeLinkSession:
    def __init__(
        self,
        socket: LinkSocket,
        writer: LinkWriter,
        *,
        stop: Callable[[int, str], None],
        store: MachineLinkStore,
        channels: RealtimeChannel,
        registry: MachineLinkRegistry,
        routes: LinkRoutes,
        runtime_token: Callable[[UUID], str],
        bundle_version: Callable[[], str | None],
        heartbeat_ms: int = AGENT_HOST_LINK_HEARTBEAT_MS,
        push_runtime: Callable[[ComputeLinkSession], Awaitable[None]] = push_runtime,
    ) -> None:
        self.session_id = uuid7()
        self._socket = socket
        self._writer = writer
        self._stop = stop
        self._store = store
        self._channels = channels
        self._registry = registry
        self._routes = routes
        self._runtime_token = runtime_token
        self._bundle_version = bundle_version
        self._heartbeat_ms = heartbeat_ms
        self._silence_limit = heartbeat_ms * AGENT_HOST_LINK_MISSED_HEARTBEATS / 1000
        self._push_runtime = push_runtime
        self._tasks: set[asyncio.Task[None]] = set()
        self.connection: MachineConnection | None = None
        self.report: MachineReport | None = None
        self.generation = 0
        self.mux = Mux(writer.send_bytes, role="opener")

    @property
    def connection_id(self) -> UUID:
        assert self.connection is not None
        return self.connection.id

    async def open_stream(self, target: dict[str, object]) -> MuxStream:
        return await self.mux.open(target)

    # ------------------------------------------------------------- lifecycle

    async def serve(self, first: LinkFrame) -> None:
        """Serve the link; returns or raises ``LinkClose`` when it ends."""
        if first.type == HostFrameType.PAIR:
            await self._pair(first)
            return
        if first.type != HostFrameType.HELLO:
            raise LinkClose(
                LinkCloseCode.PROTOCOL_VIOLATION,
                "the first frame must be pair or hello",
            )
        await self._open(first)
        try:
            await self._read()
        finally:
            # Shielded and then waited for: the Agent Host session cancels this
            # task the moment the socket goes, and a cleanup cut short at its
            # first await would leave the route naming a link that is gone.
            cleanup = asyncio.ensure_future(self._close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def _close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.mux.close()
        self._registry.discard(self)
        if self.connection is not None:
            await self._routes.release(self.connection.id, session=self.session_id)
            logger.info(
                "workspace.machine_link.disconnected",
                connection_id=str(self.connection.id),
                link_session=str(self.session_id),
            )

    def _spawn(self, coroutine: Awaitable[None], name: str) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)

        def ended(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            if done.cancelled() or done.exception() is None:
                return
            logger.error(
                "workspace.machine_link.task.failed",
                connection_id=str(self.connection.id) if self.connection else None,
                link_task=name,
                exc_info=done.exception(),
            )
            self._stop(1011, "internal error")

        task.add_done_callback(ended)

    # --------------------------------------------------------------- pairing

    async def _pair(self, frame: LinkFrame) -> None:
        """Wait for the person to approve the code, then hand over the secret."""
        try:
            body = ComputePairBody.model_validate(frame.body)
        except ValidationError as exc:
            await self._writer.send_error(
                frame, LinkErrorCode.INVALID_FRAME, validation_summary(exc)
            )
            raise LinkClose(LinkCloseCode.PROTOCOL_VIOLATION, "invalid pair") from None
        self._require_protocol(body.hello.protocol_version)
        channel = pairing_channel(
            secret_hash(normalize_pairing_code(body.pairing_code))
        )
        deadline = asyncio.get_running_loop().time() + COMPUTE_PAIRING_TTL_SECONDS
        async with notice_stream(self._channels, channel) as notices:
            while True:
                try:
                    claimed = await self._store.claim_pairing(
                        code=body.pairing_code,
                        claim_key=body.claim_key,
                        installation_id=body.hello.installation_id,
                    )
                except MachineConnectionError as exc:
                    await self._writer.send_error(
                        frame, LinkErrorCode.UNAUTHORIZED, str(exc)
                    )
                    raise LinkClose(
                        LinkCloseCode.INVALID_CREDENTIAL, "pairing refused"
                    ) from None
                if claimed is not None:
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await self._writer.send_error(
                        frame, LinkErrorCode.UNAUTHORIZED, "the code expired"
                    )
                    raise LinkClose(LinkCloseCode.INVALID_CREDENTIAL, "code expired")
                if await self._peer_left(min(PAIR_RECHECK_SECONDS, remaining), notices):
                    return
        await self._writer.send_frame(
            ServerFrameType.PAIRED,
            ComputePairedBody(
                connection_id=claimed.connection.id,
                user_id=claimed.connection.user_id,
                name=claimed.connection.name,
                machine_secret=claimed.machine_secret,
            ),
            re=frame.id,
        )
        logger.info(
            "workspace.machine_link.paired",
            connection_id=str(claimed.connection.id),
            user_id=str(claimed.connection.user_id),
        )
        raise LinkClose(LinkCloseCode.NORMAL, "paired")

    async def _peer_left(self, seconds: float, notices) -> bool:
        """Wait for an approval notice or ``seconds``; True if the machine hung
        up meanwhile -- a waiting ``pair`` sends nothing, so anything it sends
        is the socket going away."""
        receive = asyncio.ensure_future(self._socket.receive())
        notice = asyncio.ensure_future(notices.next_notice(seconds))
        try:
            done, _ = await asyncio.wait(
                {receive, notice}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (receive, notice):
                task.cancel()
            await asyncio.gather(receive, notice, return_exceptions=True)
        if receive in done and not receive.cancelled() and receive.exception() is None:
            message = receive.result()
            if message.get("type") == "websocket.disconnect":
                self._writer.lost()
                return True
            raise LinkClose(LinkCloseCode.PROTOCOL_VIOLATION, "a frame during pair")
        return False

    # ----------------------------------------------------------------- hello

    @staticmethod
    def _require_protocol(version: int) -> None:
        if version < COMPUTE_MIN_PROTOCOL_VERSION:
            raise LinkClose(LinkCloseCode.UPGRADE_REQUIRED, "upgrade required")
        if version > AGENT_HOST_PROTOCOL_VERSION:
            raise LinkClose(LinkCloseCode.UPGRADE_REQUIRED, "server too old")

    async def _open(self, frame: LinkFrame) -> None:
        secret = bearer_secret(self._socket.headers)
        if secret is None:
            raise LinkClose(
                LinkCloseCode.INVALID_CREDENTIAL, "missing or malformed credential"
            )
        try:
            body = ComputeHelloBody.model_validate(frame.body)
        except ValidationError:
            raise LinkClose(
                LinkCloseCode.PROTOCOL_VIOLATION, "invalid hello frame"
            ) from None
        self._require_protocol(body.hello.protocol_version)
        try:
            opened = await self._store.open_link(
                secret=secret,
                installation_id=body.hello.installation_id,
                report=body.machine,
            )
        except TRANSIENT_ERRORS:
            raise LinkClose(LinkCloseCode.RESTARTING, "unavailable") from None
        if opened is None:
            raise LinkClose(LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON)
        self.connection, self.generation = opened
        self.report = body.machine
        await self._writer.send_frame(
            ServerFrameType.WELCOME,
            ComputeWelcomeBody(
                connection_id=self.connection.id,
                user_id=self.connection.user_id,
                name=self.connection.name,
                heartbeat_ms=self._heartbeat_ms,
                runtime=RuntimeGrant(
                    token=self._runtime_token(self.connection.id),
                    version=self._bundle_version(),
                ),
            ),
            re=frame.id,
        )
        self._registry.add(self)
        logger.info(
            "workspace.machine_link.connected",
            connection_id=str(self.connection.id),
            link_session=str(self.session_id),
            generation=self.generation,
            mode=body.machine.mode.value,
            runtime_version=body.machine.runtime_version,
        )
        self._spawn(self._watch(), "watcher")
        self._spawn(self._push_runtime(self), "runtime_push")

    # ---------------------------------------------------------------- reading

    async def _read(self) -> None:
        while True:
            try:
                message = await asyncio.wait_for(
                    self._socket.receive(), timeout=self._silence_limit
                )
            except TimeoutError:
                raise LinkClose(
                    LinkCloseCode.HEARTBEAT_TIMEOUT, "no frame from the machine"
                ) from None
            if message.get("type") == "websocket.disconnect":
                self._writer.lost()
                return
            data = message.get("bytes")
            if isinstance(data, bytes):
                try:
                    await self.mux.feed(data)
                except MuxError as error:
                    raise LinkClose(
                        LinkCloseCode.PROTOCOL_VIOLATION, f"mux: {error}"
                    ) from None
                continue
            text = message.get("text")
            if not isinstance(text, str):
                raise LinkClose(LinkCloseCode.PROTOCOL_VIOLATION, "an empty message")
            try:
                frame = LinkFrame.model_validate_json(text)
            except ValidationError:
                raise LinkClose(
                    LinkCloseCode.PROTOCOL_VIOLATION, "a frame is not a valid envelope"
                ) from None
            await self._dispatch(frame)

    async def _dispatch(self, frame: LinkFrame) -> None:
        if frame.type == HostFrameType.CONTROL:
            await self._control(frame)
        elif frame.type == HostFrameType.REVOKE:
            await self._revoke(frame)
        elif frame.type == HostFrameType.ERROR:
            log_host_reported_error(frame, host_id=self.connection_id)
        elif frame.type in {HostFrameType.PAIR, HostFrameType.HELLO}:
            raise LinkClose(
                LinkCloseCode.PROTOCOL_VIOLATION, f"{frame.type} after hello"
            )
        else:
            await self._writer.send_error(
                frame,
                LinkErrorCode.INVALID_FRAME,
                f"{frame.type!r} is not a compute frame",
            )

    async def _control(self, frame: LinkFrame) -> None:
        try:
            body = ComputeControlBody.model_validate(frame.body)
        except ValidationError as exc:
            await self._writer.send_error(
                frame, LinkErrorCode.INVALID_FRAME, validation_summary(exc)
            )
            return
        try:
            alive = await self._store.heartbeat(self.connection_id, body.machine)
        except TRANSIENT_ERRORS:
            logger.warning(
                "workspace.machine_link.heartbeat_skipped.degraded",
                connection_id=str(self.connection_id),
                exc_info=True,
            )
            alive = True
        if not alive:
            raise LinkClose(LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON)
        self.report = body.machine
        await self._writer.send_frame(
            ServerFrameType.CONTROL_OK, ComputeControlOkBody(), re=frame.id
        )

    async def _revoke(self, frame: LinkFrame) -> None:
        """``lemma machine disconnect``: the machine removing itself."""
        try:
            await self._store.revoke_self(self.connection_id)
        except MachineConnectionError:
            raise LinkClose(
                LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON
            ) from None
        await self._writer.send_frame(ServerFrameType.REVOKED, EmptyBody(), re=frame.id)
        logger.info(
            "workspace.machine_link.revoked_by_machine",
            connection_id=str(self.connection_id),
        )
        raise LinkClose(LinkCloseCode.NORMAL, "revoked")

    # --------------------------------------------------------------- watching

    async def _watch(self) -> None:
        """Own the machine, publish the route, and obey notices."""
        connection_id = self.connection_id
        ownership = LinkOwnership(
            store=self._store,
            channels=self._channels,
            connection_id=self.session_id,
            channel_for=notice_channel,
        )
        ownership.generation = self.generation
        floor = self._heartbeat_ms / 1000
        async with notice_stream(
            self._channels, notice_channel(connection_id), host_id=str(connection_id)
        ) as notices:
            newer = await ownership.claim(connection_id)
            if newer is not None:
                self._superseded(newer)
                return
            await self._routes.record(
                connection_id, session=self.session_id, generation=self.generation
            )
            while True:
                try:
                    raw = await notices.next_notice(floor)
                except StopAsyncIteration, RealtimeSlowConsumerError:
                    notices.detach()
                    raw = None
                if raw is None:
                    newer = await ownership.newer_generation(connection_id)
                    if newer is not None:
                        self._superseded(newer)
                        return
                    await self._routes.record(
                        connection_id,
                        session=self.session_id,
                        generation=self.generation,
                    )
                    continue
                notice = parse_host_notice(raw)
                if notice.kind == REVOKED:
                    logger.info(
                        "workspace.machine_link.revoked",
                        connection_id=str(connection_id),
                    )
                    self._stop(
                        LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON
                    )
                    return
                newer = ownership.superseded_by(notice)
                if newer is not None:
                    self._superseded(newer)
                    return

    def _superseded(self, newer: int) -> None:
        logger.info(
            "workspace.machine_link.superseded",
            connection_id=str(self.connection_id),
            generation=self.generation,
            newer_generation=newer,
        )
        self._stop(LinkCloseCode.SUPERSEDED, "superseded")
