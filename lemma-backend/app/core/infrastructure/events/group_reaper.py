"""Consumer groups nobody declares any more, and how to know that safely.

Nothing removes a Redis consumer group when the code that consumed it is
deleted. `surface-schedule-events` outlived PR #509 by months: zero active
consumers, a last-delivered-id frozen at the commit that deleted its subscriber,
and -- because `stream_budget._safe_minid` takes the minimum across *observed*
groups -- an XTRIM MINID watermark that could never advance past it.
`schedule_events` reached 825MB against a 256MB budget, and Redis hit `maxmemory`
twice, which took login down with it.

The hard part is not destroying the group. It is knowing that it is dead.

"Not in this process's registry" is not evidence. A process declares the topology
of the modules it was assembled with, and a deployment can install a superset of
another's. Destroying a group a different deployment still consumes deletes its
pending-entries list, and every delivery in flight with it.

So the evidence is not a registry, it is a *claim*. Every process that ensures
consumer groups already enumerates exactly the set it owns, once per
``consumer_group_reconcile_interval_seconds``; that pass now also stamps each
pair into a ledger in Redis. A group becomes a candidate only when nobody in the
fleet has claimed it for a whole grace window, and three independent liveness
facts agree: nothing pending, nothing delivered recently, no consumer that is
not idle.

Two guards are load-bearing and easy to lose:

* :func:`owned_streams` is deliberately **not** ``observable_streams()``. That
  set also carries the streaq lane queues, and streaq creates a group named
  ``workers`` on each of them. A reaper pointed at the observable set would find
  ``workers`` undeclared on every tick and destroy the job queue's
  pending-entries list, taking every in-flight job with it.
* The ledger stamps its own creation time. Until it is older than the grace
  window the reaper refuses to act at all -- otherwise the first deploy, or any
  Redis that has been flushed, sees every group unclaimed and destroys the whole
  topology.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import registered_stream_groups
from app.core.log.log import get_logger

if TYPE_CHECKING:  # the client type only; redis stays a runtime import here
    from redis.asyncio import Redis

logger = get_logger(__name__)

_CLAIMS_KEY = "lemma:stream-group-claims"
_CLAIMS_EPOCH_KEY = "lemma:stream-group-claims:since"


@dataclass(frozen=True, slots=True)
class AbandonedGroup:
    """A group that passed every liveness test, and why it is safe to remove."""

    stream: str
    group: str
    last_delivered_age_seconds: int


def _value(mapping: object, name: str, default: object = 0) -> object:
    """Read a field from Redis' reply under either key spelling.

    The reconcile loop holds a ``decode_responses=False`` client and the cron a
    decoded one, so both reach this module.
    """
    if not isinstance(mapping, dict):
        return default
    if name in mapping:
        return mapping[name]
    return mapping.get(name.encode(), default)


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _field(stream: str, group: str) -> str:
    return f"{stream}\x00{group}"


def _int(value: object) -> int:
    # Narrowed rather than cast: Redis hands back bytes, str or int depending on
    # the client's decode setting, and anything else here is a reply shape we do
    # not understand -- which is a reason to read zero, not to guess.
    if not isinstance(value, (int, str, bytes)):
        return 0
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def owned_streams() -> set[str]:
    """Only the streams this platform's own consumer groups live on.

    Deliberately not ``observable_streams()``: that set also carries the streaq
    lane queues, whose ``workers`` group is created by streaq itself and is not
    in any registry here. Reaping it would destroy the job queue's pending
    entries. `test_the_streaq_worker_group_is_out_of_scope` pins this.
    """
    return {stream for stream, _group in registered_stream_groups()}


def _delivered_age_ms(group: object, now_ms: int) -> int:
    """Age of the group's last-delivered-id, or 0 if it has never delivered.

    A Redis stream id is ``<ms>-<seq>``. A group created at ``0`` and never read
    from sits at ``0-0``, and 0 is returned for it -- *not* an enormous age. The
    caller has to decide what that means, because on its own it cannot: a group
    that has never read is either one created seconds ago by a rolling deploy or
    one whose subscriber was deleted before it ever ran, and nothing in the id
    tells them apart. The claim ledger is what does.
    """
    raw = _text(_value(group, "last-delivered-id", "")).split("-")[0]
    delivered = _int(raw)
    return 0 if delivered <= 0 else max(0, now_ms - delivered)


async def _every_consumer_idle(
    client: "Redis", stream: str, group: str, window_ms: int
) -> bool:
    """Whether no consumer has touched the group inside the window.

    An empty consumer list is the strongest form of this: nothing is attached,
    or Redis has already reaped them all.
    """
    try:
        consumers = await client.xinfo_consumers(stream, group)
    except RedisError, TypeError, ValueError:
        return False
    if not isinstance(consumers, list):
        return False
    return all(_int(_value(c, "idle", 0)) >= window_ms for c in consumers)


async def _is_abandoned(
    client: "Redis",
    stream: str,
    group: object,
    *,
    claims: dict[str, int],
    now_ms: int,
    window_ms: int,
) -> tuple[bool, int]:
    """The five-part test. Returns (abandoned, last-delivered age in seconds)."""
    name = _text(_value(group, "name", ""))
    age_ms = _delivered_age_ms(group, now_ms)
    if not name or (stream, name) in registered_stream_groups():
        return False, 0  # this process declares it
    claimed = claims.get(_field(stream, name))
    if claimed is not None and now_ms - claimed < window_ms:
        return False, 0  # someone in the fleet declares it
    if _int(_value(group, "pending", 0)) != 0:
        return False, 0  # work in flight; never destroy a PEL
    # `age_ms == 0` means it has never delivered, which is not evidence either
    # way -- a group created seconds ago looks identical to one whose subscriber
    # was deleted before it ever ran. Only a *recent* delivery saves it here;
    # the never-delivered case falls through to the ledger and the consumer
    # check, which is what can actually tell those two apart. Reading 0 as
    # "just delivered" made a group that never read unreapable forever, which
    # real Redis caught and the mocks did not.
    if age_ms and age_ms < window_ms:
        return False, 0  # it delivered something recently
    if not await _every_consumer_idle(client, stream, name, window_ms):
        return False, 0
    return True, age_ms // 1000


async def _read_ledger(client: "Redis") -> tuple[dict[str, int], int] | None:
    """The claim ledger and the moment it began, or None if it cannot be read."""
    try:
        raw_claims = await client.hgetall(_CLAIMS_KEY)
        raw_epoch = await client.get(_CLAIMS_EPOCH_KEY)
    except RedisError, TypeError, ValueError:
        logger.warning("redis.stream.group_claim_read.degraded", exc_info=True)
        return None
    epoch = _int(_text(raw_epoch)) if raw_epoch else 0
    if epoch <= 0:
        return None
    claims = {
        _text(key): _int(_text(value)) for key, value in (raw_claims or {}).items()
    }
    return claims, epoch


async def claim_registered_groups(client: "Redis") -> None:
    """Stamp every group this process owns into the shared ledger.

    One pipelined round trip per reconcile tick, bounded by the topology. A
    field that stops being renewed is exactly the signal the reaper reads, so
    this never raises: an unwritten claim costs a later reap, and the grace
    window is what keeps that safe.
    """
    pairs = sorted(registered_stream_groups())
    if not pairs:
        return
    now = int(time.time() * 1000)
    try:
        pipe = client.pipeline(transaction=False)
        pipe.set(_CLAIMS_EPOCH_KEY, now, nx=True)
        pipe.hset(_CLAIMS_KEY, mapping={_field(s, g): now for s, g in pairs})
        await pipe.execute()
    except RedisError, TypeError, ValueError:
        logger.warning(
            "redis.stream.group_claim.degraded", group_count=len(pairs), exc_info=True
        )


async def reap_abandoned_consumer_groups(
    client: "Redis",
    *,
    window_seconds: int | None = None,
    destroy: bool | None = None,
) -> list[AbandonedGroup]:
    """Report -- and, when enabled, destroy -- every abandoned consumer group.

    The two knobs fall back to settings, which is how the cron calls it. They
    are arguments so a test can say what it means instead of reaching into the
    settings object: patching ambient configuration to arrange a test is the
    habit `scripts/check_test_doubles.py` exists to discourage, and it reads
    worse besides.
    """
    window = (
        event_transport_settings.redis_stream_group_reap_after_seconds
        if window_seconds is None
        else window_seconds
    )
    if window <= 0:
        return []
    window_ms = window * 1000
    now_ms = int(time.time() * 1000)

    ledger = await _read_ledger(client)
    if ledger is None:
        return []
    claims, epoch_ms = ledger
    if now_ms - epoch_ms < window_ms:
        # The ledger is younger than the window, so everything looks unclaimed
        # only because nothing has had time to claim it. This is the whole
        # safety of a first deploy, and of a Redis that has been flushed.
        return []

    destroy_enabled = (
        event_transport_settings.redis_stream_group_destroy_enabled
        if destroy is None
        else destroy
    )
    found: list[AbandonedGroup] = []
    for stream in sorted(owned_streams()):
        try:
            groups = await client.xinfo_groups(stream)
        except RedisError, TypeError, ValueError:
            continue
        if not isinstance(groups, list):
            continue
        for group in groups:
            abandoned, age_seconds = await _is_abandoned(
                client,
                stream,
                group,
                claims=claims,
                now_ms=now_ms,
                window_ms=window_ms,
            )
            if not abandoned:
                continue
            candidate = AbandonedGroup(
                stream=stream,
                group=_text(_value(group, "name", "")),
                last_delivered_age_seconds=age_seconds,
            )
            found.append(candidate)
            logger.warning(
                "redis.stream.abandoned_consumer_group.degraded",
                stream_name=candidate.stream,
                group=candidate.group,
                last_delivered_age_seconds=candidate.last_delivered_age_seconds,
                destroyed=destroy_enabled,
            )
    if destroy_enabled:
        await _destroy(client, found)
    return found


async def _destroy(client: "Redis", groups: list[AbandonedGroup]) -> None:
    for candidate in groups:
        try:
            await client.xgroup_destroy(
                name=candidate.stream, groupname=candidate.group
            )
            await client.hdel(_CLAIMS_KEY, _field(candidate.stream, candidate.group))
        except RedisError, TypeError, ValueError:
            logger.warning(
                "redis.stream.abandoned_consumer_group_destroy.degraded",
                stream_name=candidate.stream,
                group=candidate.group,
                exc_info=True,
            )
