"""The link session's half of an ``op``: forward it to the host, relay the answer.

See docs/architecture/desktop-host-execution.md §3-4. A caller on any replica
publishes an ``OpNotice`` on the host's notice channel. Every link for that host
hears it, so the first thing a relay does is *claim* it -- one ``SET NX`` in
Redis -- because for the few seconds a reconnect leaves two links open, two
sockets could otherwise hand the host the same command twice. The claimant says
``picked_up`` on the reply channel, sends the host an ``op`` frame under an id
of its own (``s1``, ``s2``, ...), and publishes whatever comes back.

Answers are matched by ``re`` against this relay's own ids only. The host's
request ids and these never meet: an ``error`` whose ``re`` is not one of ours
is the host reporting something about its own request, and goes to the
session's ordinary handling.

Kept out of ``agent_host_link_session`` because it is a self-contained exchange
with its own bookkeeping, and the session is a lifecycle, not a router.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host_link import (
    OP_ID_PREFIX,
    ErrorBody,
    LinkErrorCode,
    LinkFrame,
    OpBody,
    OpOkBody,
    ServerFrameType,
)
from app.modules.agent.domain.agent_host_ops import (
    INVALID_ANSWER,
    LINK_LOST,
    TIMEOUT,
    OpFailure,
    OpNotice,
    OpReply,
)
from app.modules.agent.services.agent_host_link_wire import LinkWriter


logger = get_logger(__name__)

#: How long a claim outlives its op. Only has to outlast the moment two links
#: both hear the same notice.
_CLAIM_TTL_SECONDS = 60
#: How long closing waits for the relays it just failed to say so.
_CLOSE_GRACE_SECONDS = 2.0

Claim = Callable[[str], Awaitable[bool]]


async def redis_claim(op_id: str) -> bool:
    """Take an op for this link, or learn another link already has.

    Redis being unreachable reads as "claimed": the notice that brought the op
    here came through the same Redis, so this is the rare race where it went
    away between the two, and refusing would strand the caller for nothing.
    """
    try:
        client = get_redis(url=settings.redis_url)
        return bool(
            await client.set(
                f"agent-host:op:{op_id}:claim", "1", nx=True, ex=_CLAIM_TTL_SECONDS
            )
        )
    except RedisError, OSError:
        logger.warning(
            "agent.agent_host_link.op_claim_unavailable.degraded",
            op_id=op_id,
            exc_info=True,
        )
        return True


def _failure(code: str, kind: str, message: str, *, retryable: bool = False):
    return OpReply(
        type="result",
        error=OpFailure(code=code, kind=kind, message=message, retryable=retryable),
    )


class LinkOpRelay:
    """Every ``op`` one link session has in flight."""

    def __init__(
        self,
        *,
        writer: LinkWriter,
        channels: RealtimeChannel,
        claim: Claim = redis_claim,
    ) -> None:
        self._writer = writer
        self._channels = channels
        #: Public so a test can give a session's relay a claim without Redis.
        self.claim = claim
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[OpReply]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def in_flight(self) -> int:
        return len(self._pending)

    def accept(self, payload: dict[str, object] | None, *, host_id: UUID) -> None:
        """Take an ``op`` notice off the channel. Never blocks the pusher."""
        if self._closed or payload is None:
            return
        try:
            notice = OpNotice.model_validate(payload)
        except ValidationError:
            logger.warning(
                "agent.agent_host_link.op_notice_unreadable", host_id=str(host_id)
            )
            return
        task = asyncio.ensure_future(self._relay(notice, host_id=host_id))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled() or task.exception() is None:
            return
        logger.error(
            "agent.agent_host_link.op_relay.failed",
            exc_info=task.exception(),
        )

    async def _relay(self, notice: OpNotice, *, host_id: UUID) -> None:
        if not await self.claim(notice.op_id):
            return
        await self._channels.publish(notice.reply, {"type": "picked_up"})
        frame_id = f"{OP_ID_PREFIX}{next(self._ids)}"
        waiter: asyncio.Future[OpReply] = asyncio.get_running_loop().create_future()
        self._pending[frame_id] = waiter
        try:
            await self._writer.send_frame(
                ServerFrameType.OP,
                OpBody(
                    workspace=notice.workspace,
                    method=notice.method,
                    params=notice.params,
                    deadline_ms=notice.deadline_ms,
                ),
                id=frame_id,
            )
            if self._writer.peer_gone:
                reply = _failure("UNAVAILABLE", LINK_LOST, "the host link closed")
            else:
                reply = await asyncio.wait_for(
                    asyncio.shield(waiter), timeout=notice.deadline_ms / 1000
                )
        except TimeoutError:
            reply = _failure(
                LinkErrorCode.OP_FAILED.value,
                TIMEOUT,
                f"{notice.method} did not finish before its deadline",
                retryable=True,
            )
        finally:
            self._pending.pop(frame_id, None)
        logger.debug(
            "agent.agent_host_link.op_answered.diagnostic",
            host_id=str(host_id),
            method=notice.method,
            ok=reply.error is None,
        )
        await self._channels.publish(notice.reply, reply.model_dump(mode="json"))

    def answer_ok(self, frame: LinkFrame) -> bool:
        """Settle the op this ``op_ok`` answers; False if it is not ours."""
        waiter = self._pending.get(frame.re or "")
        if waiter is None:
            return False
        if waiter.done():
            return True
        try:
            body = OpOkBody.model_validate(frame.body)
        except ValidationError:
            waiter.set_result(
                _failure("INVALID_FRAME", INVALID_ANSWER, "op_ok body is invalid")
            )
            return True
        waiter.set_result(OpReply(type="result", result=body.result))
        return True

    def answer_error(self, frame: LinkFrame) -> bool:
        """Settle the op this ``error`` refuses; False if it is not ours."""
        waiter = self._pending.get(frame.re or "")
        if waiter is None:
            return False
        if waiter.done():
            return True
        try:
            body = ErrorBody.model_validate(frame.body)
        except ValidationError:
            body = ErrorBody(code="UNREADABLE")
        detail = body.detail or {}
        kind = detail.get("kind")
        waiter.set_result(
            _failure(
                body.code,
                kind if isinstance(kind, str) and kind else body.code.lower(),
                body.message,
                retryable=body.retryable,
            )
        )
        return True

    async def close(self) -> None:
        """The link is going: fail what is in flight, and let callers hear it."""
        self._closed = True
        for waiter in self._pending.values():
            if not waiter.done():
                waiter.set_result(
                    _failure(
                        "UNAVAILABLE",
                        LINK_LOST,
                        "the host link closed before the host answered",
                        retryable=True,
                    )
                )
        if not self._tasks:
            return
        _, still_running = await asyncio.wait(
            set(self._tasks), timeout=_CLOSE_GRACE_SECONDS
        )
        for task in still_running:
            task.cancel()
