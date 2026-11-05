"""Where an Agent Host's bulk bytes wait between the link and HTTP.

See ``domain/agent_host_transfer``. The op that names a transfer and the HTTP
request that moves its bytes can land on different replicas, so both meet in
Redis: one hash per transfer, expiring a minute after its op's deadline, and
one key per blob, expiring with its run's event stream.

The same bytes used to cross Redis already -- an op's ``data`` in the pub/sub
notice and reply, an image inline in the run's event stream -- so this holds
nothing Redis did not hold before; it holds it once, under a key, for a bounded
time, instead of inside a message.

Every check that has to be atomic -- whose transfer, which direction, used
once -- is one script, so two requests for the same transfer cannot both win.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.core.infrastructure.redis.client import get_redis
from app.modules.agent.domain.agent_host_transfer import (
    BLOB_TTL_SECONDS,
    sha256_hex,
)

_TO_HOST = "to_host"
_FROM_HOST = "from_host"

# What a host fetching a transfer gets: 0 nothing it may have (unknown,
# expired, another host's, or the other direction -- one answer for all, so a
# host learns nothing about transfers that are not its own), 1 already
# fetched, or 2 with the bytes and their digest. A staged body is ``reusable``:
# the frame naming it may be sent again (a resent command, a retried tool
# call), so it is fetched as often as asked until it expires.
_FETCH_SCRIPT = """
local meta = redis.call(
  'HMGET', KEYS[1], 'host', 'direction', 'data', 'sha256', 'reusable')
if not meta[1] or meta[1] ~= ARGV[1] or meta[2] ~= 'to_host' then
  return {0}
end
if meta[5] ~= '1' and redis.call('HSETNX', KEYS[1], 'taken', '1') == 0 then
  return {1}
end
return {2, meta[3], meta[4]}
"""

# What a host delivering a transfer gets: 0 nothing it may fill, 1 already
# filled, 2 more than the slot takes, 3 stored.
_DELIVER_SCRIPT = """
local meta = redis.call('HMGET', KEYS[1], 'host', 'direction', 'max_bytes')
if not meta[1] or meta[1] ~= ARGV[1] or meta[2] ~= 'from_host' then
  return 0
end
if tonumber(ARGV[2]) > tonumber(meta[3]) then
  return 2
end
if redis.call('HSETNX', KEYS[1], 'data', ARGV[4]) == 0 then
  return 1
end
redis.call('HSET', KEYS[1], 'size', ARGV[2], 'sha256', ARGV[3])
return 3
"""


class TransferRefusal(StrEnum):
    """Why a transfer request was refused, as the HTTP route answers it."""

    NOT_FOUND = "not_found"
    ALREADY_USED = "already_used"
    TOO_LARGE = "too_large"
    DIGEST_MISMATCH = "digest_mismatch"


class TransferRefused(Exception):
    def __init__(self, refusal: TransferRefusal, message: str) -> None:
        super().__init__(message)
        self.refusal = refusal
        self.message = message


@dataclass(frozen=True, slots=True)
class StagedTransfer:
    """Bytes waiting for the host to fetch: what the ``op`` names."""

    id: str
    size: int
    sha256: str

    def wire(self) -> dict[str, object]:
        return {"id": self.id, "size": self.size, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class TransferSlot:
    """An empty transfer for the host to fill: what the ``op`` names."""

    id: str
    max_bytes: int

    def wire(self) -> dict[str, object]:
        return {"id": self.id, "max_bytes": self.max_bytes}


def transfer_key(transfer_id: str) -> str:
    return f"agent-host:transfer:{transfer_id}"


def blob_key(run_id: UUID, sha256: str) -> str:
    return f"agent-host:blob:{run_id}:{sha256}"


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class AgentHostTransferStore:
    """Transfers and blobs, in Redis."""

    def __init__(self, *, client: Redis | None = None) -> None:
        # A client handed in is used as is: the unit tests give an in-process
        # Redis. Raw bytes either way -- these values are file contents.
        self._given_client = client

    def _client(self) -> Redis:
        if self._given_client is not None:
            return self._given_client
        return get_redis(decode_responses=False)

    # ------------------------------------------------- the caller's half

    async def stage_for_host(
        self, *, host_id: UUID, workspace: UUID, data: bytes, ttl_seconds: int
    ) -> StagedTransfer:
        """Hold ``data`` for ``host_id`` to fetch once."""
        staged = StagedTransfer(id=uuid4().hex, size=len(data), sha256=sha256_hex(data))
        key = transfer_key(staged.id)
        async with self._client().pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "host": str(host_id),
                    "workspace": str(workspace),
                    "direction": _TO_HOST,
                    "size": staged.size,
                    "sha256": staged.sha256,
                    "data": data,
                },
            )
            pipe.expire(key, max(1, ttl_seconds))
            await pipe.execute()
        return staged

    async def stage_body_for_host(
        self, *, host_id: UUID, body: bytes, ttl_seconds: int
    ) -> StagedTransfer:
        """Hold part of a frame Lemma sends ``host_id`` for it to fetch instead.

        Reusable until it expires, unlike an op's transfer: the frame that
        names it may be sent again, and every copy has to resolve.
        """
        staged = StagedTransfer(id=uuid4().hex, size=len(body), sha256=sha256_hex(body))
        key = transfer_key(staged.id)
        async with self._client().pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "host": str(host_id),
                    "direction": _TO_HOST,
                    "size": staged.size,
                    "sha256": staged.sha256,
                    "data": body,
                    "reusable": "1",
                },
            )
            pipe.expire(key, max(1, ttl_seconds))
            await pipe.execute()
        return staged

    async def open_for_host(
        self, *, host_id: UUID, workspace: UUID, max_bytes: int, ttl_seconds: int
    ) -> TransferSlot:
        """An empty transfer ``host_id`` may fill once, with at most ``max_bytes``."""
        slot = TransferSlot(id=uuid4().hex, max_bytes=max_bytes)
        key = transfer_key(slot.id)
        async with self._client().pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "host": str(host_id),
                    "workspace": str(workspace),
                    "direction": _FROM_HOST,
                    "max_bytes": max_bytes,
                },
            )
            pipe.expire(key, max(1, ttl_seconds))
            await pipe.execute()
        return slot

    async def collect(self, transfer_id: str, *, size: int, sha256: str) -> bytes:
        """What the host delivered into a slot, checked against what it said.

        ``size`` and ``sha256`` are the op's answer. They have to agree with
        what arrived over HTTP: the answer and the bytes take separate paths,
        and an answer for bytes that never arrived is not a read.
        """
        key = transfer_key(transfer_id)
        data, stored_sha = await self._client().hmget(key, ["data", "sha256"])
        await self.discard(transfer_id)
        if data is None:
            raise TransferRefused(
                TransferRefusal.NOT_FOUND,
                "the host answered without delivering the data",
            )
        if len(data) != size or _text(stored_sha) != sha256:
            raise TransferRefused(
                TransferRefusal.DIGEST_MISMATCH,
                "the data the host delivered is not the data it described",
            )
        return bytes(data)

    async def discard(self, transfer_id: str) -> None:
        await self._client().delete(transfer_key(transfer_id))

    # --------------------------------------------------- the host's half

    async def max_bytes_for(self, transfer_id: str, *, host_id: UUID) -> int:
        """How much ``host_id`` may deliver into this slot, before it sends any."""
        host, direction, max_bytes = await self._client().hmget(
            transfer_key(transfer_id), ["host", "direction", "max_bytes"]
        )
        if (
            host is None
            or _text(host) != str(host_id)
            or _text(direction) != _FROM_HOST
            or max_bytes is None
        ):
            raise TransferRefused(TransferRefusal.NOT_FOUND, "no such transfer")
        return int(max_bytes)

    async def fetch_by_host(
        self, transfer_id: str, *, host_id: UUID
    ) -> tuple[bytes, str]:
        """The staged bytes and their SHA-256, once, to the host they are for."""
        answer = await self._client().eval(
            _FETCH_SCRIPT, 1, transfer_key(transfer_id), str(host_id)
        )
        status = int(answer[0])
        if status == 0:
            raise TransferRefused(TransferRefusal.NOT_FOUND, "no such transfer")
        if status == 1:
            raise TransferRefused(
                TransferRefusal.ALREADY_USED, "this transfer was already fetched"
            )
        return bytes(answer[1]), _text(answer[2])

    async def deliver_by_host(
        self, transfer_id: str, *, host_id: UUID, data: bytes, sha256: str
    ) -> None:
        """Fill a slot, once, from the host it is for, with the bytes it named."""
        if sha256_hex(data) != sha256:
            raise TransferRefused(
                TransferRefusal.DIGEST_MISMATCH, "the body does not match its digest"
            )
        status = int(
            await self._client().eval(
                _DELIVER_SCRIPT,
                1,
                transfer_key(transfer_id),
                str(host_id),
                len(data),
                sha256,
                data,
            )
        )
        if status == 0:
            raise TransferRefused(TransferRefusal.NOT_FOUND, "no such transfer")
        if status == 1:
            raise TransferRefused(
                TransferRefusal.ALREADY_USED, "this transfer was already delivered"
            )
        if status == 2:
            raise TransferRefused(
                TransferRefusal.TOO_LARGE, "more data than this transfer takes"
            )

    # ------------------------------------------------------------ blobs

    async def put_blob(self, *, run_id: UUID, sha256: str, data: bytes) -> None:
        """Keep a run event's attachment, named by its own digest.

        Writing the same blob again is harmless and expected: a host that
        resends a run's events after a lost stream uploads its blobs again.
        """
        if sha256_hex(data) != sha256:
            raise TransferRefused(
                TransferRefusal.DIGEST_MISMATCH, "the body does not match its digest"
            )
        await self._client().set(blob_key(run_id, sha256), data, ex=BLOB_TTL_SECONDS)

    async def get_blob(self, *, run_id: UUID, sha256: str) -> bytes | None:
        data = await self._client().get(blob_key(run_id, sha256))
        return bytes(data) if data is not None else None


__all__ = [
    "AgentHostTransferStore",
    "StagedTransfer",
    "TransferRefusal",
    "TransferRefused",
    "TransferSlot",
    "blob_key",
    "transfer_key",
]
