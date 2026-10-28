"""The pushing half of one Agent Host link.

One task per connection, for the life of the socket. It listens on the host's
notice channel -- one subscription -- and reads the command queue on every
poke and every floor tick, pushing what it finds. It also hears ``superseded``
and ``revoked`` and ends the connection for them, and on every floor tick reads
the host's link generation again, so a lost notice cannot leave two links
pushing for one host. Supersession is decided by the link generation each
``hello`` claims in the database, never by which notice arrived: see
``link/ownership``.

Split from the session, which owns reading the socket; the two share only the
writer, the ops relay and the session's ``stop``.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.log.log import get_logger
from app.modules.agent.agent_host.domain.link import (
    REVOKED_OR_MISSING_REASON,
    CommandsBody,
    LinkCloseCode,
    ServerFrameType,
)
from app.modules.agent.agent_host.control.channels import (
    OP,
    REVOKED,
    HostNotice,
    host_poke_channel,
    parse_host_notice,
)
from app.modules.agent.agent_host.link.mcp_relay import notice_stream
from app.modules.agent.agent_host.link.ops import LinkOpRelay
from app.modules.agent.agent_host.link.outbound import (
    OutboundStager,
    command_frames,
)
from app.modules.agent.agent_host.link.ownership import LinkOwnership
from app.modules.agent.agent_host.link.store import AgentHostLinkStore
from app.modules.agent.agent_host.link.wire import (
    TRANSIENT_ERRORS,
    LinkWriter,
    OutboundFrameTooLarge,
    SentCommands,
)


logger = get_logger(__name__)


class LinkPusher:
    """Commands down to the host, and the notices that end a connection."""

    def __init__(
        self,
        *,
        store: AgentHostLinkStore,
        channels: RealtimeChannel,
        writer: LinkWriter,
        ops: LinkOpRelay,
        ownership: LinkOwnership,
        sent: SentCommands,
        connection_id: UUID,
        push_floor_seconds: float,
        available_runs: Callable[[], int],
        stop: Callable[[int, str], None],
        stager: OutboundStager | None = None,
    ) -> None:
        self._store = store
        self._channels = channels
        self._writer = writer
        self._ops = ops
        self._ownership = ownership
        self._sent = sent
        self._connection_id = connection_id
        self._push_floor_seconds = push_floor_seconds
        self._available_runs = available_runs
        self._stop = stop
        self._stager = stager

    async def push_loop(self, host_id: UUID) -> None:
        """Push commands on every poke and every floor tick; obey notices."""
        async with notice_stream(
            self._channels, host_poke_channel(host_id), host_id=str(host_id)
        ) as notices:
            newer = await self._ownership.claim(host_id)
            if newer is not None:
                self._superseded(host_id, newer)
                return
            while True:
                try:
                    raw = await notices.next_notice(self._push_floor_seconds)
                except StopAsyncIteration, RealtimeSlowConsumerError:
                    logger.warning(
                        "agent.agent_host_link.notices_lost.degraded",
                        host_id=str(host_id),
                    )
                    notices.detach()
                    raw = None
                if raw is None and await self._lost_the_host(host_id):
                    return
                notice = parse_host_notice(raw) if raw is not None else None
                if notice is not None and notice.kind == OP:
                    # An op is not a reason to re-read the command queue.
                    self._ops.accept(notice.payload, host_id=host_id)
                    continue
                if notice is not None and self._obey(host_id, notice):
                    return
                await self._push_commands(host_id)

    async def _lost_the_host(self, host_id: UUID) -> bool:
        """On a floor tick: whether a newer link now owns the host."""
        newer = await self._ownership.newer_generation(host_id)
        if newer is None:
            return False
        self._superseded(host_id, newer)
        return True

    def _obey(self, host_id: UUID, notice: HostNotice) -> bool:
        """Act on a notice; True when it ended this connection."""
        if notice.kind == REVOKED:
            self._stop(LinkCloseCode.REVOKED_OR_MISSING, REVOKED_OR_MISSING_REASON)
            return True
        newer = self._ownership.superseded_by(notice)
        if newer is not None:
            self._superseded(host_id, newer, by=notice.connection_id)
            return True
        return False

    def _superseded(
        self, host_id: UUID, generation: int, *, by: str | None = None
    ) -> None:
        logger.info(
            "agent.agent_host_link.superseded",
            host_id=str(host_id),
            connection_id=str(self._connection_id),
            link_generation=self._ownership.generation,
            superseded_by_generation=generation,
            superseded_by=by,
        )
        self._stop(LinkCloseCode.SUPERSEDED, "superseded")

    async def _push_commands(self, host_id: UUID) -> None:
        try:
            commands = await self._store.read_commands(
                host_id=host_id, available_run_slots=self._available_runs()
            )
        except TRANSIENT_ERRORS:
            logger.warning(
                "agent.agent_host_link.push_skipped.degraded",
                host_id=str(host_id),
                exc_info=True,
            )
            return
        fresh = self._sent.unsent(commands)
        try:
            frames = await command_frames(self._stager, fresh, host_id=host_id)
        except TRANSIENT_ERRORS:
            # Staging a payload needs Redis; the next push tries again.
            self._sent.forget([command.command_id for command in fresh])
            logger.warning(
                "agent.agent_host_link.push_skipped.degraded",
                host_id=str(host_id),
                exc_info=True,
            )
            return
        for frame in frames:
            try:
                await self._writer.send_frame(
                    ServerFrameType.COMMANDS, CommandsBody(commands=frame)
                )
            except OutboundFrameTooLarge:
                # Logged by the writer. Packing keeps every frame under the
                # ceiling, so this is a command no host can be sent.
                continue


__all__ = ["LinkPusher"]
