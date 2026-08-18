"""Dead-letter a stream message that can never succeed, instead of retrying it forever.

Redis Streams do not drop a message the consumer never acknowledges. It stays in
the pending-entries list, and the reclaim subscriber hands it back every minute
for as long as the deployment lives. For a *transient* failure that is exactly
what you want. For a message that can never be processed it is a permanent
background load and permanent log noise, and it drowns the error rate that real
failures have to show up in.

That is not theoretical. One malformed publish to ``surface_events`` ran at ~119
redeliveries an hour in development, off two stuck messages that had each been
delivered more than 680 times.

Two ways out, and both are needed:

* **Known-permanent errors** — a payload that fails validation will fail
  validation identically on every redelivery. There is no point in a second
  attempt, let alone a thousandth. Quarantined on the first failure.
* **A delivery-count backstop** — for everything else. A "transient" failure that
  is really permanent (a bug on a code path only this message reaches) would
  otherwise loop forever, so a message that has failed enough times is
  quarantined whatever the error looked like.

The conservative direction is deliberate: the permanent set is *closed* and small,
and anything unrecognised is re-raised so normal redelivery still gets its
chances. A message is only ever given up on because it provably cannot succeed,
or because it has already had more attempts than any real transient fault needs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from faststream import BaseMiddleware
from pydantic import ValidationError
from redis.typing import EncodableT, FieldT

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import (
    registered_groups_for_stream,
)
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Errors that will recur identically on every redelivery. Closed set on
#: purpose: anything not named here is re-raised and retried as normal.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    ValidationError,
    json.JSONDecodeError,
    UnicodeDecodeError,
)

#: Failures before a message is given up on regardless of the error. Well above
#: any real transient fault (a Redis failover or a database restart resolves in
#: far fewer), and far below the 680+ redeliveries the surface_events poison
#: messages had accumulated.
MAX_DELIVERY_ATTEMPTS = 12

#: Failure counters outlive a redelivery cycle but not a deployment.
_FAILURE_COUNTER_TTL_SECONDS = 24 * 60 * 60


def dead_letter_stream(stream: str) -> str:
    return f"{stream}:dead"


def _failure_key(stream: str, message_id: str) -> str:
    return f"lemma:stream-failure:{stream}:{message_id}"


def is_permanent(error: BaseException) -> bool:
    """True when re-delivering the same bytes cannot produce a different result."""
    if isinstance(error, PERMANENT_ERRORS):
        return True
    # fast-depends wraps the pydantic failure raised while solving the handler's
    # signature; the cause is what says whether this can ever succeed.
    cause = error.__cause__ or error.__context__
    return isinstance(cause, PERMANENT_ERRORS) if cause is not None else False


def describe_message(msg: Any) -> tuple[str, str]:
    """Best-effort ``(stream, message_id)`` for a FastStream Redis message.

    Defensive rather than trusting: this runs on the failure path, and a
    quarantine that itself raises would put us back in the loop it exists to
    break.
    """
    raw = getattr(msg, "raw_message", None) or {}
    stream = ""
    if isinstance(raw, dict):
        channel = raw.get("channel") or raw.get("stream") or ""
        stream = channel.decode() if isinstance(channel, bytes) else str(channel)
    message_id = str(getattr(msg, "message_id", "") or "")
    return stream, message_id


class StreamQuarantineMiddleware(BaseMiddleware):
    """Ack and dead-letter a message that cannot succeed, instead of nacking it.

    Sits inside FastStream's acknowledgement middleware, so swallowing the
    exception here is what lets the message be acked and finally leave the
    pending-entries list. Re-raising leaves the existing behaviour untouched.

    Placed at the broker rather than around each handler on purpose: the failure
    that started this happened during *decoding*, before any handler body ran, so
    a per-handler wrapper could not have seen it.
    """

    async def consume_scope(self, call_next: Any, msg: Any) -> Any:
        try:
            return await call_next(msg)
        except Exception as error:
            if not await self._should_quarantine(msg, error):
                raise
            await self._quarantine(msg, error)
            # Swallowed: the ack middleware above now sees a clean return and
            # acknowledges, which is the only thing that clears the PEL entry.
            return None

    async def _should_quarantine(self, msg: Any, error: Exception) -> bool:
        if is_permanent(error):
            return True
        return await self._record_failure(msg) >= MAX_DELIVERY_ATTEMPTS

    async def _record_failure(self, msg: Any) -> int:
        """Count this message's failures, so a looping one eventually stops."""
        stream, message_id = describe_message(msg)
        if not message_id:
            return 0
        try:
            client = get_redis()
            key = _failure_key(stream, message_id)
            count = int(await client.incr(key))
            if count == 1:
                await client.expire(key, _FAILURE_COUNTER_TTL_SECONDS)
            return count
        except Exception:
            # Counting is best-effort. Losing the count means the message keeps
            # retrying, which is the old behaviour, not a new failure.
            logger.debug("events.quarantine.counter_unavailable", exc_info=True)
            return 0

    @staticmethod
    def _declared_groups(stream: str) -> str:
        """Which consumer groups read this stream, from the subscriber registry.

        FastStream does not put the group on the message — it is subscriber
        configuration, handed to ``ack()`` rather than carried by the entry — so
        it cannot be read back off the failure. The registry is the honest
        source, and it names every group on the stream rather than guessing
        which one gave up.
        """
        if not stream:
            return ""
        return ",".join(sorted(registered_groups_for_stream(stream)))

    async def _quarantine(self, msg: Any, error: Exception) -> None:
        stream, message_id = describe_message(msg)
        target = dead_letter_stream(stream or "unknown")
        body = getattr(msg, "body", b"")
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")

        # Annotated rather than inferred: redis-py's `xadd` takes an invariant
        # `Dict[FieldT, EncodableT]`, so a plain `dict[str, str]` is rejected.
        entry: dict[FieldT, EncodableT] = {
            "original_stream": stream,
            "consumer_groups": self._declared_groups(stream),
            "message_id": message_id,
            "error_type": type(error).__name__,
            "error_message": str(error)[:2000],
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "body": str(body)[:64_000],
        }

        try:
            client = get_redis()
            maxlen = event_transport_settings.stream_maxlen_for(target)
            await client.xadd(
                target,
                entry,
                maxlen=maxlen,
                approximate=True,
            )
        except Exception:
            # If the dead-letter write fails the message is still acked, because
            # re-raising here would restore the infinite loop. The log line below
            # is then the only record, which is why it carries the body.
            logger.error(
                "events.quarantine.dead_letter_write_failed",
                original_stream=stream,
                message_id=message_id,
                error_type=type(error).__name__,
                exc_info=True,
            )

        logger.warning(
            "events.quarantine.message_dead_lettered",
            original_stream=stream,
            dead_letter_stream=target,
            consumer_groups=entry["consumer_groups"],
            message_id=message_id,
            error_type=entry["error_type"],
            error_message=entry["error_message"],
        )
