"""The caller's half of an ``op``: send one operation to a host, wait for it.

See docs/architecture/desktop-host-execution.md §3 and ``domain/agent_host_ops``
for the two Redis messages. The reply channel is subscribed *before* the notice
is published, so an answer can never arrive ahead of the listener.

Two clocks. ``OP_PICKUP_TIMEOUT_SECONDS`` bounds how long nobody may answer
before the host counts as offline -- short, because a live link answers in
milliseconds and an agent should not sit for a whole deadline on a Mac that is
asleep. The op's own deadline bounds the rest.

An op that moves file data names a transfer instead of carrying it
(``domain/agent_host_transfer``): ``send`` stages the bytes for the host to
fetch, ``receive`` opens a slot for the host to fill and collects it once the
host has answered. Either way the bytes cross HTTP, never the link or the
Redis messages that carry the op.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.log.log import get_logger
from app.modules.agent.agent_host.domain.link import (
    OP_MAX_DEADLINE_MS,
    OP_PICKUP_TIMEOUT_SECONDS,
)
from app.modules.agent.agent_host.domain.transfer import (
    TRANSFER_GRACE_SECONDS,
    TRANSFER_MAX_BYTES,
)
from app.modules.agent.agent_host.domain.ops import (
    TRANSFER_INCOMPLETE,
    HOST_OFFLINE,
    HOST_OFFLINE_MESSAGE,
    INVALID_ANSWER,
    LINK_UNAVAILABLE,
    TIMEOUT,
    AgentHostOpError,
    OpNotice,
    OpReply,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.agent_host.control.channels import (
    host_poke_channel,
    op_reply_channel,
)
from app.modules.agent.agent_host.control.transfers import (
    AgentHostTransferStore,
    TransferRefused,
)
from app.modules.agent.agent_host.link.mcp_relay import NoticeStream


logger = get_logger(__name__)

#: Beyond the op's own deadline, how long the caller waits for the relay to say
#: that the deadline passed. The relay owns that verdict; this only stops a
#: relay that died mid-op from holding the caller for ever.
_ANSWER_GRACE_SECONDS = 5.0


class AgentHostOpClient:
    """Sends ``op`` requests to a host's link, from any replica."""

    def __init__(
        self,
        channels: RealtimeChannel | None = None,
        *,
        pickup_timeout_seconds: float = OP_PICKUP_TIMEOUT_SECONDS,
        transfers: AgentHostTransferStore | None = None,
    ) -> None:
        self._channels = channels
        self._pickup_timeout = pickup_timeout_seconds
        self._transfers = transfers or AgentHostTransferStore()

    async def _channel(self) -> RealtimeChannel:
        if self._channels is None:
            self._channels = await get_channel_service()
        return self._channels

    async def request(
        self,
        *,
        host_id: UUID,
        workspace: UUID,
        method: str,
        params: JsonObject,
        deadline_at: datetime,
    ) -> JsonObject:
        """The method's ``result``, or ``AgentHostOpError`` saying why not."""
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        deadline_ms = max(1, min(OP_MAX_DEADLINE_MS, int(remaining * 1000)))
        op_id = uuid4().hex
        notice = OpNotice(
            op_id=op_id,
            reply=op_reply_channel(op_id),
            workspace=workspace,
            method=method,
            params=params,
            deadline_ms=deadline_ms,
        )
        try:
            channels = await self._channel()
            async with channels.subscribe([notice.reply]) as messages:
                await channels.publish(
                    host_poke_channel(host_id), notice.model_dump(mode="json")
                )
                stream = NoticeStream(messages)
                try:
                    return await self._answer(stream, notice)
                finally:
                    stream.close()
        except (RedisError, OSError, RealtimeSlowConsumerError) as exc:
            logger.warning(
                "agent.agent_host_ops.request_unavailable.degraded",
                host_id=str(host_id),
                method=method,
                exc_info=True,
            )
            raise AgentHostOpError(
                LINK_UNAVAILABLE,
                "Lemma could not reach this Mac's link just now.",
                retryable=True,
            ) from exc

    async def send(
        self,
        *,
        host_id: UUID,
        workspace: UUID,
        method: str,
        params: JsonObject,
        data: bytes,
        deadline_at: datetime,
    ) -> JsonObject:
        """An op whose data the host fetches over HTTP: ``file.write``."""
        if len(data) > TRANSFER_MAX_BYTES:
            raise ValueError(f"one transfer carries at most {TRANSFER_MAX_BYTES} bytes")
        staged = await self._transfer_call(
            self._transfers.stage_for_host(
                host_id=host_id,
                workspace=workspace,
                data=data,
                ttl_seconds=_transfer_ttl(deadline_at),
            ),
            host_id=host_id,
            method=method,
        )
        try:
            return await self.request(
                host_id=host_id,
                workspace=workspace,
                method=method,
                params={**params, "transfer": staged.wire()},
                deadline_at=deadline_at,
            )
        finally:
            await self._discard(staged.id)

    async def receive(
        self,
        *,
        host_id: UUID,
        workspace: UUID,
        method: str,
        params: JsonObject,
        max_bytes: int,
        deadline_at: datetime,
    ) -> tuple[JsonObject, bytes]:
        """An op whose data the host delivers over HTTP: ``file.read``.

        The host answers with the ``size`` and ``sha256`` of what it delivered;
        the bytes are what the slot holds, and only if the two agree.
        """
        slot = await self._transfer_call(
            self._transfers.open_for_host(
                host_id=host_id,
                workspace=workspace,
                max_bytes=min(max_bytes, TRANSFER_MAX_BYTES),
                ttl_seconds=_transfer_ttl(deadline_at),
            ),
            host_id=host_id,
            method=method,
        )
        try:
            result = await self.request(
                host_id=host_id,
                workspace=workspace,
                method=method,
                params={**params, "transfer": slot.wire()},
                deadline_at=deadline_at,
            )
            size, sha256 = result.get("size"), result.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int):
                raise AgentHostOpError(
                    INVALID_ANSWER,
                    f"This Mac answered {method} without saying what it delivered.",
                )
            if size == 0:
                return result, b""
            if not isinstance(sha256, str):
                raise AgentHostOpError(
                    INVALID_ANSWER,
                    f"This Mac answered {method} without a digest of its data.",
                )
            try:
                data = await self._transfer_call(
                    self._transfers.collect(slot.id, size=size, sha256=sha256),
                    host_id=host_id,
                    method=method,
                )
            except TransferRefused as refused:
                raise AgentHostOpError(
                    TRANSFER_INCOMPLETE,
                    f"The data this Mac sent for {method} did not arrive intact: "
                    f"{refused.message}.",
                    retryable=True,
                ) from refused
            return result, data
        finally:
            await self._discard(slot.id)

    async def _transfer_call[T](
        self, call: Awaitable[T], *, host_id: UUID, method: str
    ) -> T:
        try:
            return await call
        except (RedisError, OSError) as exc:
            logger.warning(
                "agent.agent_host_ops.transfer_unavailable.degraded",
                host_id=str(host_id),
                method=method,
                exc_info=True,
            )
            raise AgentHostOpError(
                LINK_UNAVAILABLE,
                "Lemma could not hold this Mac's file data just now.",
                retryable=True,
            ) from exc

    async def _discard(self, transfer_id: str) -> None:
        """Best effort: a transfer left behind still expires on its own."""
        try:
            await self._transfers.discard(transfer_id)
        except RedisError, OSError:
            logger.warning(
                "agent.agent_host_ops.transfer_discard_skipped.degraded",
                exc_info=True,
            )

    async def _answer(self, stream: NoticeStream, notice: OpNotice) -> JsonObject:
        loop = asyncio.get_running_loop()
        picked_up = False
        pickup_by = loop.time() + self._pickup_timeout
        answer_by = loop.time() + notice.deadline_ms / 1000 + _ANSWER_GRACE_SECONDS
        while True:
            limit = answer_by if picked_up else pickup_by
            raw = await stream.next_notice(max(0.0, limit - loop.time()))
            if raw is None:
                if not picked_up:
                    raise AgentHostOpError(HOST_OFFLINE, HOST_OFFLINE_MESSAGE)
                raise AgentHostOpError(
                    TIMEOUT,
                    f"This Mac took {notice.method} but did not answer in time.",
                    retryable=True,
                )
            try:
                reply = OpReply.model_validate_json(raw)
            except ValidationError:
                raise AgentHostOpError(
                    INVALID_ANSWER, "This Mac's answer could not be read."
                ) from None
            if reply.type == "picked_up":
                picked_up = True
                continue
            if reply.error is not None:
                raise AgentHostOpError(
                    reply.error.kind,
                    reply.error.message or reply.error.kind,
                    retryable=reply.error.retryable,
                )
            return reply.result or {}


def _transfer_ttl(deadline_at: datetime) -> int:
    """How long a transfer lives: its op's deadline, and a minute's grace."""
    remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
    return max(1, int(remaining)) + TRANSFER_GRACE_SECONDS
