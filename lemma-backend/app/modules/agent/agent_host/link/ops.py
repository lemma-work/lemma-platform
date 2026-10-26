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

Kept out of ``link/session`` because it is a self-contained exchange
with its own bookkeeping, and the session is a lifecycle, not a router.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import itertools
from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.server_limits import WS_MAX_BYTES
from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent.agent_host.domain.link import (
    OP_ID_PREFIX,
    ErrorBody,
    LinkErrorCode,
    LinkFrame,
    OpBody,
    OpOkBody,
    ServerFrameType,
)
from app.modules.agent.agent_host.domain.ops import (
    INVALID_ANSWER,
    LINK_LOST,
    LINK_UNAVAILABLE,
    TIMEOUT,
    TRANSFER_INCOMPLETE,
    OpFailure,
    OpNotice,
    OpReply,
)
from app.modules.agent.agent_host.domain.transfer import (
    LINK_INLINE_DATA_BYTES,
    sha256_hex,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.agent_host.control.transfers import (
    AgentHostTransferStore,
    TransferRefusal,
    TransferRefused,
)
from app.modules.agent.agent_host.link.wire import (
    LinkWriter,
    OutboundFrameTooLarge,
)


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
        transfers: AgentHostTransferStore | None = None,
    ) -> None:
        self._writer = writer
        self._channels = channels
        #: Public so a test can give a session's relay a claim without Redis.
        self.claim = claim
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[OpReply]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        #: Whether the host on this link moves file data itself. The session
        #: sets it from ``hello``; until then, and for a host released before
        #: transfers, this relay moves the data for it (``OldHostTransfers``).
        self.host_moves_transfers = False
        self._old_host = OldHostTransfers(transfers)

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
        transfer = None if self.host_moves_transfers else op_transfer(notice.params)
        try:
            params = (
                notice.params
                if self.host_moves_transfers
                else old_host_params(notice.method, notice.params)
            )
            if transfer is not None:
                params = await self._old_host.outbound(
                    notice.method, params, transfer, host_id=host_id
                )
            await self._writer.send_frame(
                ServerFrameType.OP,
                OpBody(
                    workspace=notice.workspace,
                    method=notice.method,
                    params=params,
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
            if transfer is not None and reply.result is not None:
                reply = OpReply(
                    type="result",
                    result=await self._old_host.inbound(
                        notice.method, reply.result, transfer, host_id=host_id
                    ),
                )
        except TimeoutError:
            reply = _failure(
                LinkErrorCode.OP_FAILED.value,
                TIMEOUT,
                f"{notice.method} did not finish before its deadline",
                retryable=True,
            )
        except OutboundFrameTooLarge as refused:
            reply = _failure(
                LinkErrorCode.OP_FAILED.value,
                "too_large",
                f"{notice.method}: {refused}",
            )
        except TransferRefused as refused:
            reply = _failure(
                LinkErrorCode.OP_FAILED.value,
                TRANSFER_INCOMPLETE,
                f"{notice.method}: {refused.message}",
                retryable=True,
            )
        except RedisError, OSError:
            logger.warning(
                "agent.agent_host_link.old_host_transfer_unavailable.degraded",
                host_id=str(host_id),
                method=notice.method,
                exc_info=True,
            )
            reply = _failure(
                "UNAVAILABLE",
                LINK_UNAVAILABLE,
                f"{notice.method}: Lemma could not hold the file data just now",
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


#: The most one byte of a process's output can cost in an old host's
#: ``process.read`` answer: every chunk is ``{"sequence":…,"stream":…,"data":…}``
#: with its data base64'd, and a process that flushes a byte at a time makes a
#: chunk of each. 72 covers a 20-digit sequence, ``stderr`` and JSON spacing.
_OLD_HOST_WORST_BYTES_PER_OUTPUT_BYTE = 72
#: The answer's own envelope -- ``op_ok``, its id, the state, the cursors.
_OLD_HOST_REPLY_HEADROOM = 4 * 1024
#: What a host released before transfers keeps of a process's output between
#: two reads: as much as still fits the message ceiling in the worst case.
OLD_HOST_OUTPUT_LIMIT_BYTES = (
    WS_MAX_BYTES - _OLD_HOST_REPLY_HEADROOM
) // _OLD_HOST_WORST_BYTES_PER_OUTPUT_BYTE


def old_host_params(method: str, params: JsonObject) -> JsonObject:
    """An op's params as a host released before transfers is sent them.

    Such a host ignores ``process.read``'s ``max_bytes`` and answers a read
    with everything its process kept since the last one, a JSON object per
    chunk. What it keeps is bounded by the ``output_limit_bytes`` it is started
    with (newest kept, the rest reported dropped), and only in raw bytes -- so
    the bound is the one that keeps even a byte-per-chunk answer inside the
    API's message ceiling (``OLD_HOST_OUTPUT_LIMIT_BYTES``). Past it the link
    would close with 1009 mid-run; under it a burst between two reads loses
    its oldest part, which the reader is told (``process_output``).
    """
    if method != "process.start":
        return params
    asked = params.get("output_limit_bytes")
    limit = OLD_HOST_OUTPUT_LIMIT_BYTES
    # ``True`` is an ``int`` in Python; it is not a byte count.
    if isinstance(asked, int) and not isinstance(asked, bool) and asked > 0:
        limit = asked
    return {**params, "output_limit_bytes": min(limit, OLD_HOST_OUTPUT_LIMIT_BYTES)}


def op_transfer(params: JsonObject) -> JsonObject | None:
    """The transfer an op names, if it names one."""
    transfer = params.get("transfer")
    return transfer if isinstance(transfer, dict) else None


class OldHostTransfers:
    """File data for a host released before transfers, moved by this relay.

    Such a host reads ``data`` off a ``file.write`` and puts it on a
    ``file.read``'s answer, and knows nothing of transfers -- a write that
    named one would reach its exec-server with no data at all. So on its link
    the relay does the host's half here, where the bytes are a Redis read
    away: it fetches a write's chunk into the op, and delivers a read's
    answer into the slot. The caller sees the same transfer either way.

    A write's chunk travels to the host inline, as it always did; only what
    the host *sends* meets the API's message ceiling, so a read asks for no
    more than ``LINK_INLINE_DATA_BYTES``, which base64 keeps inside it. Its
    ``process.read`` answers are bounded where the process starts
    (``old_host_params``).
    """

    def __init__(self, store: AgentHostTransferStore | None = None) -> None:
        self._store = store or AgentHostTransferStore()

    async def outbound(
        self,
        method: str,
        params: JsonObject,
        transfer: JsonObject,
        *,
        host_id: UUID,
    ) -> JsonObject:
        """The op as the old host reads it."""
        inline = {key: value for key, value in params.items() if key != "transfer"}
        if method == "file.write":
            data, _ = await self._store.fetch_by_host(
                str(transfer.get("id")), host_id=host_id
            )
            inline["data"] = base64.b64encode(data).decode("ascii")
        elif method == "file.read":
            length = inline.get("length")
            asked = length if isinstance(length, int) else LINK_INLINE_DATA_BYTES
            inline["length"] = max(0, min(asked, LINK_INLINE_DATA_BYTES))
        return inline

    async def inbound(
        self,
        method: str,
        result: JsonObject,
        transfer: JsonObject,
        *,
        host_id: UUID,
    ) -> JsonObject:
        """The old host's answer as the caller reads it."""
        if method != "file.read":
            return result
        answer = {key: value for key, value in result.items() if key != "data"}
        encoded = result.get("data")
        try:
            data = base64.b64decode(encoded) if isinstance(encoded, str) else b""
        except binascii.Error, ValueError:
            raise TransferRefused(
                TransferRefusal.DIGEST_MISMATCH, "the host's data is not base64"
            ) from None
        digest = sha256_hex(data)
        if data:
            await self._store.deliver_by_host(
                str(transfer.get("id")), host_id=host_id, data=data, sha256=digest
            )
        answer["size"] = len(data)
        answer["sha256"] = digest
        return answer
