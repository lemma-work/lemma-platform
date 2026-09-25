"""The caller's half of an ``op``: send one operation to a host, wait for it.

See docs/architecture/desktop-host-execution.md §3 and ``domain/agent_host_ops``
for the two Redis messages. The reply channel is subscribed *before* the notice
is published, so an answer can never arrive ahead of the listener.

Two clocks. ``OP_PICKUP_TIMEOUT_SECONDS`` bounds how long nobody may answer
before the host counts as offline -- short, because a live link answers in
milliseconds and an agent should not sit for a whole deadline on a Mac that is
asleep. The op's own deadline bounds the rest.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host_link import (
    OP_MAX_DEADLINE_MS,
    OP_PICKUP_TIMEOUT_SECONDS,
)
from app.modules.agent.domain.agent_host_ops import (
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
from app.modules.agent.infrastructure.agent_host.channels import (
    host_poke_channel,
    op_reply_channel,
)
from app.modules.agent.services.agent_host_link_mcp import NoticeStream


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
    ) -> None:
        self._channels = channels
        self._pickup_timeout = pickup_timeout_seconds

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
