"""Enqueue work on the caller's open transaction.

A work item commits with the state change that causes it or not at all: a
rollback erases both, and there is no "publish after commit" to lose. This is
the property whose absence made every reconcile cron necessary.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.infrastructure.work.db import side_connection
from app.core.infrastructure.work.limits import WorkLimitPort, current_work_limit_port
from app.core.infrastructure.work.rates import rates_json
from app.core.infrastructure.work.registry import (
    WorkRegistry,
    registry as default_registry,
)
from app.core.infrastructure.work.types import WorkClass, validate_namespace

_INSERT_ITEM = text(
    """
    INSERT INTO work_items (id, ns, class, kind, payload, dedup_key,
                            concurrency_key, concurrency_limit, rate_key,
                            run_at, priority)
    VALUES (:id, :ns, :cls, :kind, CAST(:payload AS jsonb), :dedup_key,
            :concurrency_key, :concurrency_limit, :rate_key,
            COALESCE(CAST(:run_at AS timestamptz), now())
              + make_interval(secs => :delay_seconds),
            :priority)
    ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued'
    DO NOTHING
    RETURNING id
    """
)

_QUEUE_EXISTS = text("SELECT 1 FROM work_queues WHERE class = :cls AND ns = :ns")
# Only for a row that does not exist yet, and never in the caller's
# transaction: inserting a *new* key into a unique index waits for any other
# uncommitted insert of that key, so two business transactions each creating
# queue rows for two new namespaces in opposite orders deadlock. Committed on
# its own connection, the row is never held across anyone's transaction; if
# the caller then rolls back, an empty queue row is left for retention.
_ENSURE_QUEUE = text(
    """
    INSERT INTO work_queues (class, ns, max_running, rates)
    VALUES (:cls, :ns, :cap, CAST(:rates AS jsonb))
    ON CONFLICT (class, ns) DO NOTHING
    """
)

_DROP_QUEUED = text("DELETE FROM work_items WHERE ns = :ns AND state = 'queued'")
_CANCEL_QUEUED = text(
    "DELETE FROM work_items"
    " WHERE dedup_key = :dedup_key AND state = 'queued' AND ns = :ns"
)

# Postgres folds identical notifications within one transaction into one.
_NOTIFY = text("SELECT pg_notify(:channel, '')")


#: An item due later than this is found by the dispatchers' poll; waking them
#: now would only make them look and find nothing due.
_WAKE_HORIZON = timedelta(seconds=1)


def _due_soon(run_at: datetime | None, delay: timedelta | None) -> bool:
    """Whether an item is due soon enough to be worth a wake.

    NOTIFY takes a database-wide lock at commit, so every notifying commit is
    serialised behind the others; a burst of transactions that each arm a
    future timer (a TIME schedule's next fire) paid that for nothing.
    """
    later = delay or timedelta(0)
    if run_at is not None:
        later += run_at - datetime.now(UTC)
    return later <= _WAKE_HORIZON


def wake_channel(cls: WorkClass) -> str:
    return f"work_{cls.value}"


#: Set while a work handler runs: how to wake this process's own dispatcher
#: of a class. Work a handler enqueues wakes that, after its transaction
#: commits, instead of sending a NOTIFY -- a burst of handler commits that each
#: notify serialises on NOTIFY's database-wide commit lock, and the
#: dispatchers it would wake are already looping or will find it on their
#: poll. Requests from the API are the ones a NOTIFY is for.
LocalWake = Callable[[WorkClass], None]
_handler_wake: ContextVar[LocalWake | None] = ContextVar(
    "work_handler_wake", default=None
)


@contextmanager
def waking_locally(wake: LocalWake) -> Iterator[None]:
    """Within this block, enqueued work wakes ``wake`` after commit, never NOTIFY."""
    token = _handler_wake.set(wake)
    try:
        yield
    finally:
        _handler_wake.reset(token)


class HasSession(Protocol):
    @property
    def session(self) -> AsyncSession: ...


class WorkWriter:
    """Adds work items in ``session``'s current transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        registry: WorkRegistry | None = None,
        limits: WorkLimitPort | None = None,
    ) -> None:
        self._session = session
        self._registry = registry or default_registry
        self._limits = limits
        self._queues_ensured: set[tuple[WorkClass, str]] = set()
        self._notified: set[WorkClass] = set()

    @classmethod
    def of(cls, uow: HasSession, *, registry: WorkRegistry | None = None) -> WorkWriter:
        return cls(uow.session, registry=registry)

    @property
    def registry(self) -> WorkRegistry:
        return self._registry

    async def add(
        self,
        kind: str,
        ns: str,
        payload: BaseModel | Mapping[str, JsonValue] | None = None,
        *,
        dedup_key: str | None = None,
        run_at: datetime | None = None,
        delay: timedelta | None = None,
        concurrency_key: str | None = None,
        rate_key: str | None = None,
    ) -> UUID | None:
        """Enqueue ``kind`` for namespace ``ns``.

        Returns the new item's id, or ``None`` when ``dedup_key`` coalesced it
        into an item already queued. The payload is validated against the
        kind's model here, so a malformed item fails its writer rather than
        dying later in a worker.

        ``rate_key`` picks the bucket for the kind's per-key rates (a schedule
        id); without one they fall back to ``concurrency_key``.
        """
        spec = self._registry.get(kind)
        validate_namespace(ns)
        if concurrency_key is not None and spec.concurrency is None:
            raise ValueError(f"work kind {kind!r} declares no concurrency limit")
        if rate_key is not None and not spec.rate_per_key:
            raise ValueError(f"work kind {kind!r} declares no per-key rate")
        if spec.rate_per_key and rate_key is None and concurrency_key is None:
            raise ValueError(
                f"work kind {kind!r} has a per-key rate: pass rate_key or concurrency_key"
            )
        body = _payload_json(spec.payload_type, payload)
        item_id = await self._session.scalar(
            _INSERT_ITEM,
            {
                "id": uuid7(),
                "ns": ns,
                "cls": spec.cls.value,
                "kind": kind,
                "payload": body,
                "dedup_key": dedup_key,
                "concurrency_key": concurrency_key,
                "concurrency_limit": spec.concurrency if concurrency_key else None,
                "rate_key": rate_key,
                "run_at": run_at,
                "delay_seconds": (delay or timedelta(0)).total_seconds(),
                "priority": spec.priority,
            },
        )
        await self._ensure_queue(spec.cls, ns)
        if _due_soon(run_at, delay):
            await self._notify(spec.cls)
        return item_id

    async def drop_queued(self, ns: str) -> int:
        """Delete every queued item of ``ns`` in this transaction; returns how many.

        For a namespace whose owner is going away (a deleted pod): its waiting
        work has nothing left to act on. Running items are left alone -- they
        finish, or lose their lease and find their subject gone -- and dead ones
        stay for inspection until retention. Work that must outlive the
        namespace is written under another one (the pod's ``org:``) after this.
        """
        validate_namespace(ns)
        result = await self._session.execute(_DROP_QUEUED, {"ns": ns})
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def _ensure_queue(self, cls: WorkClass, ns: str) -> None:
        """Make sure (class, ns) has a queue row, without locking it in this transaction.

        The common case is an existing, committed row: a plain read, no lock,
        so concurrent writers in one pod never serialise on it and a claim
        holding it ``FOR UPDATE`` never blocks an enqueue.
        """
        if (cls, ns) in self._queues_ensured:
            return
        key = {"cls": cls.value, "ns": ns}
        if await self._session.scalar(_QUEUE_EXISTS, key) is None:
            limits = await (self._limits or current_work_limit_port()).limits_for(ns)
            await self._create_queue(
                {**key, "cap": limits.cap(cls), "rates": rates_json(limits.rates)}
            )
        self._queues_ensured.add((cls, ns))

    async def _create_queue(self, params: dict[str, object]) -> None:
        bind = self._session.bind
        if not isinstance(bind, AsyncEngine):
            # A session bound to a single connection has no second one to use.
            await self._session.execute(_ENSURE_QUEUE, params)
            return
        async with side_connection(bind) as connection:
            await connection.execute(_ENSURE_QUEUE, params)
            await connection.commit()

    async def cancel(self, ns: str, dedup_key: str) -> int:
        """Delete the queued item ``dedup_key`` names, in this transaction.

        For an item whose reason went away -- a wait's expiry once the wait
        resolved: left queued, it would still be claimed, only to find nothing
        to do. A running item is left alone; its handler re-checks.
        """
        validate_namespace(ns)
        result = await self._session.execute(
            _CANCEL_QUEUED, {"ns": ns, "dedup_key": dedup_key}
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def _notify(self, cls: WorkClass) -> None:
        if cls in self._notified:
            return
        self._notified.add(cls)
        local = _handler_wake.get()
        if local is not None:
            event.listen(
                self._session.sync_session,
                "after_commit",
                lambda _session: local(cls),
                once=True,
            )
            return
        await self._session.execute(_NOTIFY, {"channel": wake_channel(cls)})


def _payload_json(
    model: type[BaseModel], payload: BaseModel | Mapping[str, JsonValue] | None
) -> str:
    if isinstance(payload, BaseModel):
        validated = model.model_validate(payload.model_dump(mode="json"))
    else:
        validated = model.model_validate(dict(payload or {}))
    return json.dumps(validated.model_dump(mode="json"), separators=(",", ":"))
