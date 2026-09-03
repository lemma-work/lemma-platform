from __future__ import annotations

import inspect
from dataclasses import dataclass

from faststream.redis import StreamSub

from app.core.infrastructure.events.config import event_transport_settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Every grouped stream subscriber registers its (stream, group) here at import
# time (the decorators call redis_stream_sub). The worker uses this to keep the
# Redis consumer groups alive — see ensure_consumer_groups below.
_REGISTERED_STREAM_GROUPS: set[tuple[str, str]] = set()
_DECLARED_STREAM_GROUPS: set[tuple[str, str]] = set()


@dataclass(frozen=True, slots=True)
class SubscriberEventBinding:
    """How one stream subscriber declared the event it receives.

    Recorded so a test can assert the whole population takes ``dict``. A Redis
    Stream has no server-side type filter, so every consumer group on a shared
    stream receives every event published to it and must sort them out itself.
    Annotating the parameter with a concrete event model instead moves
    validation into fast-depends, ahead of the acknowledgement — and a message
    that cannot validate can never be acked, so it stays in the pending-entries
    list and the reclaim subscriber hands it back every minute, forever.

    That is not a hypothetical failure mode. It ran in development at ~119
    redeliveries an hour, and it grew by one permanently-stuck message per agent
    created. See ``test_stream_subscriber_contract``.
    """

    stream: str
    group: str
    handler: str
    annotation: str


_SUBSCRIBER_EVENT_BINDINGS: list[SubscriberEventBinding] = []


def _record_event_binding(handler, *, stream: str, group: str) -> None:
    """Capture the first parameter's annotation for the convention gate."""
    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except TypeError, ValueError:  # pragma: no cover - not a plain function
        return
    if not parameters:
        return
    annotation = parameters[0].annotation
    _SUBSCRIBER_EVENT_BINDINGS.append(
        SubscriberEventBinding(
            stream=stream,
            group=group,
            handler=f"{getattr(handler, '__module__', '?')}.{getattr(handler, '__qualname__', handler)}",
            # ``from __future__ import annotations`` makes these strings in the
            # handler modules; normalise the rare non-string case so the gate
            # compares like with like.
            annotation=(
                annotation
                if isinstance(annotation, str)
                else getattr(annotation, "__name__", repr(annotation))
            ),
        )
    )


def registered_subscriber_event_bindings() -> list[SubscriberEventBinding]:
    """Every (stream, group, handler, annotation) seen at decoration time."""
    return list(_SUBSCRIBER_EVENT_BINDINGS)


class ConsumerGroupTopologyError(RuntimeError):
    """A declared Redis Stream consumer group could not be ensured."""


def declare_stream_groups(groups) -> None:
    """Add module-declared stream/group relationships to the process topology."""
    _DECLARED_STREAM_GROUPS.update(groups)


def redis_stream_sub(
    stream: str,
    *,
    group: str | None = None,
    consumer: str | None = None,
) -> StreamSub:
    """Create a Redis Stream subscriber with a shared polling interval."""
    if group:
        _REGISTERED_STREAM_GROUPS.add((stream, group))
    return StreamSub(
        stream,
        group=group,
        consumer=consumer,
        polling_interval=event_transport_settings.redis_stream_polling_interval_ms,
    )


def redis_stream_reclaim_sub(
    stream: str,
    *,
    group: str,
    consumer: str,
) -> StreamSub:
    """Build the companion subscriber that only reclaims abandoned deliveries.

    FastStream's ``min_idle_time`` mode uses XAUTOCLAIM exclusively and does not
    read new ``>`` entries. It must therefore run alongside, never instead of,
    the normal XREADGROUP subscriber.
    """
    _REGISTERED_STREAM_GROUPS.add((stream, group))
    return StreamSub(
        stream,
        group=group,
        consumer=f"{consumer}-reclaimer",
        polling_interval=event_transport_settings.redis_stream_polling_interval_ms,
        min_idle_time=event_transport_settings.redis_stream_min_idle_time_ms,
    )


def reliable_redis_stream_subscriber(
    router,
    stream: str,
    *,
    group: str,
    consumer: str,
):
    """Register normal delivery plus 60-second abandoned-message reclaim."""

    def decorator(handler):
        _record_event_binding(handler, stream=stream, group=group)
        normal = router.subscriber(
            stream=redis_stream_sub(stream, group=group, consumer=consumer)
        )(handler)
        router.subscriber(
            stream=redis_stream_reclaim_sub(
                stream,
                group=group,
                consumer=consumer,
            )
        )(handler)
        return normal

    return decorator


def registered_stream_groups() -> set[tuple[str, str]]:
    """All (stream, group) pairs declared by grouped stream subscribers."""
    return set(_REGISTERED_STREAM_GROUPS | _DECLARED_STREAM_GROUPS)


def registered_groups_for_stream(stream: str) -> set[str]:
    """Return the declared consumer groups for one static stream name."""
    return {
        group
        for declared_stream, group in registered_stream_groups()
        if declared_stream == stream
    }


async def ensure_stream_groups(redis_client, stream: str) -> int:
    """Strictly ensure every declared group for one stream before publication."""
    created = 0
    groups = sorted(
        group
        for declared_stream, group in registered_stream_groups()
        if declared_stream == stream
    )
    for group in groups:
        try:
            await redis_client.xgroup_create(
                name=stream,
                groupname=group,
                id="$",
                mkstream=True,
            )
            created += 1
        except Exception as exc:
            if "BUSYGROUP" in str(exc):
                continue
            raise ConsumerGroupTopologyError(
                f"Could not ensure consumer group {group!r} for stream {stream!r}"
            ) from exc
    return created


#: How many stream/group names one log record names before it stops listing.
#: The whole topology is a couple of dozen pairs; a record that named every one
#: of them would be cut at the field bound anyway, so the cut is made here
#: where the count can be carried alongside it.
_MAX_NAMED_GROUPS = 12


def _describe(pairs: list[tuple[str, str]]) -> str:
    """``stream/group`` names for a log record, bounded and ordered."""
    listed = [f"{stream}/{group}" for stream, group in sorted(pairs)]
    if len(listed) > _MAX_NAMED_GROUPS:
        remaining = len(listed) - _MAX_NAMED_GROUPS
        listed = [*listed[:_MAX_NAMED_GROUPS], f"(+{remaining} more)"]
    return ", ".join(listed)


async def ensure_consumer_groups(
    redis_client, *, warn_on_create: bool = True, only_stream: str | None = None
) -> int:
    """Idempotently (re)create every registered Redis consumer group.

    Returns the number of groups (re)created. Two cases need this:

    1. **Startup race.** Multiple subscribers can share one stream (e.g. both the
       workflow and surface subscribers consume ``schedule_events``). At
       ``broker.start`` FastStream races to create each group, and a subscriber
       that issues XREADGROUP before its group exists gets NOGROUP, which ends
       its consume task; FastStream's supervisor restarts that task at once and
       with no backoff, so the subscriber spins on NOGROUP until the group is
       back. Calling this once before ``broker.start`` pre-creates every group
       so no subscriber races. Pass ``warn_on_create=False`` there: creating a
       group on a fresh (or flushed) Redis is expected, not an anomaly.
    2. **Mid-run loss.** If a group is later lost — Redis flush, failover to an
       un-replicated replica, key eviction, or stream trim — recreating it on a
       short interval lets the retrying subscriber's next attempt succeed
       without a restart.

    Groups are created at ``$`` (new messages only): after a data-loss event the
    old entries are gone anyway, and this avoids reprocessing a whole surviving
    stream. Never raises — group plumbing must not crash the worker.

    Case 2 is reported at WARNING and an ensure failure at ERROR. Both used to
    be ``logger.debug``, which ``LOG_LEVEL=INFO`` drops before formatting, so
    the one accident that stops delivery on a self-host left an empty log. They
    are rare by construction — a group is created once per stream per Redis
    lifetime — so this is not a volume trade, and the whole pass reports once
    rather than once per group so a Redis outage cannot turn a tick into a
    burst.
    """
    pairs = [
        (stream, group)
        for stream, group in registered_stream_groups()
        if only_stream is None or stream == only_stream
    ]
    return await _ensure_groups(redis_client, pairs, warn_on_create=warn_on_create)


async def _ensure_groups(
    redis_client, pairs: list[tuple[str, str]], *, warn_on_create: bool
) -> int:
    created: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    first_failure: Exception | None = None
    for stream, group in pairs:
        try:
            await redis_client.xgroup_create(
                name=stream, groupname=group, id="$", mkstream=True
            )
            created.append((stream, group))
        except Exception as exc:  # BUSYGROUP (already exists) is the happy path
            if "BUSYGROUP" in str(exc):
                continue
            failed.append((stream, group))
            if first_failure is None:
                first_failure = exc
    if created and warn_on_create:
        # A group that had to be *re*created was gone, and while it was gone its
        # subscribers consumed nothing. Naming the groups is the point: it says
        # which streams stopped being delivered.
        logger.warning(
            "infrastructure.stream_subscriber.recreated_missing_consumer_groups.degraded",
            group_count=len(created),
            groups=_describe(created),
        )
    elif created:
        logger.debug(
            "infrastructure.stream_subscriber.created_consumer_groups.observed",
            group_count=len(created),
            groups=_describe(created),
        )
    if first_failure is not None:
        # Reported once for the pass, with the first exception's traceback: the
        # rest of a failing pass is the same Redis saying the same thing, and a
        # type name without a traceback is an error nobody can act on. Spelled
        # as a triple because the record is emitted after the handler has
        # exited, where `exc_info=True` has nothing left to read.
        logger.error(
            "infrastructure.stream_subscriber.consumer_group_ensure.failed",
            group_count=len(failed),
            groups=_describe(failed),
            error_type=type(first_failure).__name__,
            exc_info=(
                type(first_failure),
                first_failure,
                first_failure.__traceback__,
            ),
        )
    return len(created)
