"""A per-pod token that changes whenever what the pod holds does.

An agent's runtime brief lists the pod's tables, agents, people, workflows,
functions and top-level files, and is cached because building it takes several
reads on every run. Kept on a timer alone, the cache went on showing the pod as
it was before a change for as long as the entry lived -- an agent that had just
created a table read a brief without it. A cached brief now records the token it
was built under, and any committed write to a row that appears in the brief
replaces the token: a different token is a miss.

Which rows count is declared on the model, not here, so no module's write path
has to remember to call anything: a mapped class sets ``__pod_inventory__`` to
the attribute holding its pod's id (``"pod_id"``, or ``"id"`` on the pod
itself). A flush about to add, change or delete one queues its pod -- before
it, while a deleted row can still say which pod it was in; the
transaction's commit replaces the token. A bulk ``UPDATE``/``DELETE`` statement
bypasses the flush and is not seen; the cache's own TTL bounds that case.

In Redis, because the writer and the reader are usually different processes. A
token that cannot be read is reported as unknown, and a caller treats unknown as
"do not use the cache", so Redis being down costs a rebuild, never a stale
brief.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from functools import lru_cache
from uuid import UUID, uuid4

from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.orm import Session, UOWTransaction

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

logger = get_logger(__name__)

#: The class attribute that marks a model as part of a pod's inventory.
MARKER = "__pod_inventory__"
#: The token a pod has before anything wrote to it since the token expired.
NEVER_CHANGED = "0"
#: Far longer than anything caches against it.
_TOKEN_TTL_SECONDS = 7 * 24 * 3600
#: Where a session keeps the pods its transaction changed.
_SESSION_KEY = "lemma_pod_inventory_changed"
#: Bumps in flight per process; past it one is dropped and the TTL bounds it.
_MAX_PENDING = 1000

_pending: set[asyncio.Task[None]] = set()  # memory: bounded by _MAX_PENDING


@lru_cache(maxsize=1)
def _tokens() -> RedisJsonCache[str]:
    return RedisJsonCache(
        redis_url=settings.redis_url,
        key_prefix="pod:inventory-generation",
        ttl_seconds=_TOKEN_TTL_SECONDS,
    )


async def pod_inventory_generation(pod_id: UUID) -> str | None:
    """The pod's current token, or ``None`` when it cannot be read."""
    try:
        token = await _tokens().get_json(str(pod_id))
    except RedisError, OSError, TimeoutError:
        logger.warning(
            "pod_inventory.generation.read_failed.degraded",
            pod_id=str(pod_id),
            exc_info=True,
        )
        return None
    return token if isinstance(token, str) else NEVER_CHANGED


async def bump_pod_inventory(pod_ids: Iterable[UUID]) -> None:
    """Replace each pod's token now."""
    for pod_id in pod_ids:
        try:
            await _tokens().set_json(str(pod_id), uuid4().hex)
        except RedisError, OSError, TimeoutError:
            logger.warning(
                "pod_inventory.generation.bump_failed.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )


def _pod_of(instance: object) -> UUID | None:
    attribute = getattr(type(instance), MARKER, None)
    if not isinstance(attribute, str):
        return None
    value = getattr(instance, attribute, None)
    return value if isinstance(value, UUID) else None


def _queue_changed(
    session: Session, _flush: UOWTransaction, _instances: object
) -> None:
    changed = {
        pod_id
        for instance in (*session.new, *session.dirty, *session.deleted)
        if (pod_id := _pod_of(instance)) is not None
    }
    if not changed:
        return
    session.info.setdefault(_SESSION_KEY, set()).update(changed)
    # Kept for the session's life rather than ``once``: a session commits
    # more than once, and a ``once`` listener stays registered without firing
    # again, so a second ``listen`` of it is a silent no-op.
    if not event.contains(session, "after_commit", _bump_committed):
        event.listen(session, "after_commit", _bump_committed)
        event.listen(session, "after_rollback", _forget)


def _bump_committed(session: Session) -> None:
    queued: set[UUID] = session.info.pop(_SESSION_KEY, None) or set()
    if queued:
        _schedule_bump(sorted(queued))


def _schedule_bump(queued: list[UUID]) -> None:
    """Bump in the background: ``after_commit`` is synchronous."""
    if len(_pending) >= _MAX_PENDING:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # A synchronous session outside the app: nothing caches a brief there.
        return
    task = create_background_task(bump_pod_inventory(queued), name="pod-inventory-bump")
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def _forget(session: Session) -> None:
    session.info.pop(_SESSION_KEY, None)


def install() -> None:
    """Watch every session's flushes; idempotent."""
    if not event.contains(Session, "before_flush", _queue_changed):
        event.listen(Session, "before_flush", _queue_changed)


async def settle() -> None:
    """Wait for the bumps already queued: for a test reading right after a write."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


__all__ = [
    "MARKER",
    "NEVER_CHANGED",
    "bump_pod_inventory",
    "install",
    "pod_inventory_generation",
    "settle",
]
