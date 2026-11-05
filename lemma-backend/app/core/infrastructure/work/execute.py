"""Running a claimed item's handler, and settling whatever it did.

A handler is arbitrary code, so this is the one place the runtime catches
everything (``capture``): an exception from a handler is data -- it decides
between retry, backoff and dead -- never a reason for a dispatcher to stop.
Cancellation is not an exception here and always propagates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError

from app.core.infrastructure.db.transient_errors import (
    is_database_unavailable,
    is_schema_skew,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.db import SessionMaker, WorkDb
from app.core.infrastructure.work.lifecycle import (
    apply_decision,
    finish_item,
    lock_owned,
    mark_dead,
    release,
)
from app.core.infrastructure.work.registry import HandlerSpec, WorkRegistry
from app.core.infrastructure.work.retry import (
    AttemptState,
    Dead,
    Decision,
    after_defer,
    after_failure,
    after_permanent,
    after_unavailable,
    describe_error,
)
from app.core.infrastructure.work.types import (
    ClaimedItem,
    Defer,
    MaxRuntimeExceeded,
    PermanentFailure,
    Unavailable,
    UnavailableEvidence,
    database_down,
)
from app.core.infrastructure.work.writer import LocalWake, WorkWriter
from app.core.log.log import get_logger

logger = get_logger(__name__)

__all__ = ["ExecEnv", "SessionMaker", "capture", "run_fast", "settle"]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ExecEnv:
    """What every handler run in one dispatcher shares."""

    #: Every session the runtime opens comes from here, counted per kind.
    db: WorkDb
    registry: WorkRegistry
    owner: str
    statement_timeout_ms: int
    #: Set when the process starts shutting down; jobs poll it at safe points.
    stopping: asyncio.Event = field(default_factory=asyncio.Event)
    clock: Callable[[], datetime] = utcnow
    #: Wakes this process's dispatcher of a class; what work a handler
    #: enqueues uses instead of NOTIFY. A class this process does not run is
    #: found by its dispatchers' poll.
    local_wake: LocalWake = field(default=lambda _cls: None)


async def capture(awaitable: Awaitable[object]) -> Exception | None:
    """Await ``awaitable`` and return what it raised instead of raising it."""
    try:
        await awaitable
    except Exception as exc:  # noqa: BLE001 - the handler boundary; classified by decide()
        return exc
    return None


def _is_timeout(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    # SQLSTATE 57014, query_canceled: what statement_timeout raises.
    return (
        isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "57014"
    )


#: How long a missing column or table is taken for a rollout in progress.
#: A release's code-and-schema window is minutes; this outlasts a slow one.
SCHEMA_SKEW_WINDOW = timedelta(minutes=20)


def _rollout_may_still_be_running(state: AttemptState, now: datetime) -> bool:
    return (
        state.first_unavailable_at is None
        or now - state.first_unavailable_at <= SCHEMA_SKEW_WINDOW
    )


def decide(
    spec: HandlerSpec, state: AttemptState, exc: Exception, now: datetime
) -> Decision:
    policy = spec.retry
    if isinstance(exc, Unavailable):
        return after_unavailable(policy, state, exc, now=now)
    if is_database_unavailable(exc):
        # The database itself could not be reached or take a connection: the
        # same evidence an adapter's ``Unavailable`` carries, so the same
        # uncharged retry, bounded by the kind's unavailable budget -- not an
        # attempt the item is blamed for.
        return after_unavailable(policy, state, database_down(exc), now=now)
    if is_schema_skew(exc) and _rollout_may_still_be_running(state, now):
        # A release runs each version of the code against the other version of
        # the schema for a few minutes; an item that hits that waits it out
        # uncharged. Past the window it is taken for a real bug and charged; a
        # charge ends the streak, so a real one is dead after ``max_attempts``
        # windows.
        skew = Unavailable(
            f"schema skew: {type(exc).__name__}",
            evidence=UnavailableEvidence.SCHEMA_SKEW,
        )
        skew.__cause__ = exc
        return after_unavailable(policy, state, skew, now=now)
    if isinstance(exc, Defer):
        return after_defer(state, exc.delay, now=now)
    if isinstance(exc, MaxRuntimeExceeded):
        return after_permanent(state, reason="max_runtime", error=describe_error(exc))
    if isinstance(exc, PermanentFailure):
        return after_permanent(state, reason="permanent", error=describe_error(exc))
    if isinstance(exc, ValidationError):
        return after_permanent(
            state, reason="invalid_payload", error=describe_error(exc)
        )
    error_class = "timeout" if _is_timeout(exc) else "failure"
    return after_failure(
        policy, state, now=now, error=describe_error(exc), error_class=error_class
    )


def validated_payload(spec: HandlerSpec, item: ClaimedItem) -> BaseModel:
    return spec.payload_type.model_validate(item.payload)


async def settle(
    env: ExecEnv, spec: HandlerSpec, item: ClaimedItem, exc: Exception
) -> str:
    """Record a failed run, fenced: requeue with backoff, or dead with its hook."""
    async with env.db.handler(item.cls) as session:
        uow = SqlAlchemyUnitOfWork(session)
        state = await lock_owned(session, item, env.owner)
        if state is None:
            return "lease_lost"
        decision = decide(spec, state, exc, env.clock())
        if isinstance(exc, Defer):
            # "Not now", not a failure: a per-pod cap held until a run ends
            # defers thousands of times under load, and logging each as a
            # failed item with its traceback buried the real ones.
            logger.debug("work.item.deferred", kind=item.kind, ns=item.ns)
        else:
            logger.warning(
                "work.item.failed",
                item_id=str(item.id),
                kind=item.kind,
                ns=item.ns,
                error_class=_error_class(decision),
                attempts=decision.attempts,
                exc_info=exc,
            )
        hook_error = await capture(
            apply_decision(uow, item, decision, spec, env.registry)
        )
        if hook_error is None:
            await uow.commit()
            return "dead" if isinstance(decision, Dead) else "retry"
    if not isinstance(decision, Dead):
        # A requeue runs no domain code; failing here is the database failing.
        raise hook_error
    await _dead_without_hook(env, item, decision, hook_error)
    return "dead"


def _error_class(decision: Decision) -> str | None:
    return decision.reason if isinstance(decision, Dead) else decision.error_class


async def _dead_without_hook(
    env: ExecEnv, item: ClaimedItem, decision: Dead, hook_error: Exception
) -> None:
    """The kind's ``on_dead`` raised: still go dead, and say loudly that the domain did not hear."""
    logger.error(
        "work.item.on_dead_failed",
        item_id=str(item.id),
        kind=item.kind,
        ns=item.ns,
        exc_info=hook_error,
    )
    async with env.db.handler(item.cls) as session:
        uow = SqlAlchemyUnitOfWork(session)
        if await lock_owned(session, item, env.owner) is None:
            return
        reason = Dead(
            f"{decision.reason}:on_dead_failed",
            decision.attempts,
            decision.lease_losses,
            decision.last_error,
        )
        await mark_dead(uow, item, reason, None, env.registry)
        await uow.commit()


async def release_item(env: ExecEnv, item: ClaimedItem) -> str:
    async with env.db.handler(item.cls) as session:
        released = await release(
            session, item.id, env.owner, now=env.clock(), epoch=item.lease_epoch
        )
        await session.commit()
    return "released" if released else "lease_lost"


async def _apply_and_finish(
    env: ExecEnv,
    spec: HandlerSpec,
    item: ClaimedItem,
    uow: SqlAlchemyUnitOfWork,
    payload: BaseModel,
) -> None:
    work = WorkWriter(uow.session, registry=env.registry)
    await spec.handler(FastContext(uow=uow, item=item, work=work), payload)
    await finish_item(uow.session, item, env.registry)
    await uow.commit()


async def run_fast(env: ExecEnv, spec: HandlerSpec, item: ClaimedItem) -> str:
    """Effect and completion in one transaction, under the item's row lock.

    A crash anywhere rolls both back and leaves the item running under a lease
    that will expire; a zombie whose lease was taken over finds no row to lock
    and does nothing. That is what makes a fast handler's DB effect
    exactly-once without an inbox.
    """
    error: Exception | None
    async with env.db.handler(item.cls) as session:
        uow = SqlAlchemyUnitOfWork(session)
        if (
            await lock_owned(
                session,
                item,
                env.owner,
                statement_timeout_ms=env.statement_timeout_ms,
            )
            is None
        ):
            logger.warning(
                "work.item.lease_lost", item_id=str(item.id), kind=item.kind, ns=item.ns
            )
            return "lease_lost"
        error = await capture(_validated_then(env, spec, item, uow))
        if error is None:
            return "done"
        await uow.rollback()
    return await settle(env, spec, item, error)


async def _validated_then(
    env: ExecEnv, spec: HandlerSpec, item: ClaimedItem, uow: SqlAlchemyUnitOfWork
) -> None:
    payload = validated_payload(spec, item)
    await _apply_and_finish(env, spec, item, uow, payload)
