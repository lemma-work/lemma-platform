"""Every connection the runtime takes, counted, so it never waits on its own pool.

The runtime's database use falls into four kinds, each behind its own
semaphore, and the runtime's pool is sized to their sum:

* ``handler`` (per class, ``WORK_DB_SLOTS_JSON``): a fast handler's whole
  transaction; a job's checkpoints, fenced completion and settling.
* ``dispatch`` (one per class): the claim and the lease reaper, which a
  dispatcher runs one after the other, never together.
* ``control`` (``WORK_DB_CONTROL_SLOTS``): heartbeats, gauges, cap refresh,
  arming periodics -- short statements that must not queue behind handlers,
  or a busy class would let its own leases lapse.
* ``side`` (one): the separate, immediately committed connection that
  creates a namespace's first queue row (see ``WorkWriter``). It is the only
  kind taken while another is held, and nothing is ever taken while holding
  it, so the nesting cannot deadlock.

A handler opening a second handler session while holding one would hold a
slot while waiting for another; that raises ``NestedWorkSession`` instead of
hanging.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core.infrastructure.work.config import class_db_slots, work_settings
from app.core.infrastructure.work.types import WorkClass

type SessionMaker = Callable[[], AsyncSession]
type AbstractSessionContext = AbstractAsyncContextManager[AsyncSession]

_SIDE_SLOTS = 1

#: The side-connection semaphore of the runtime whose session this task is in.
_side_gate: ContextVar[asyncio.Semaphore | None] = ContextVar(
    "work_side_gate", default=None
)
#: Whether this task already holds a handler session.
_in_handler_session: ContextVar[bool] = ContextVar(
    "work_in_handler_session", default=False
)


class NestedWorkSession(RuntimeError):
    """A handler session was opened inside another one in the same task."""


@dataclass(frozen=True, slots=True)
class DbBudget:
    handler: Mapping[WorkClass, int]
    control: int

    @classmethod
    def from_settings(cls, classes: list[WorkClass]) -> DbBudget:
        return cls(
            handler={c: class_db_slots(c) for c in classes},
            control=work_settings.work_db_control_slots,
        )

    @property
    def total(self) -> int:
        """Connections the runtime can hold at once, at most."""
        dispatch = len(self.handler)
        return sum(self.handler.values()) + dispatch + self.control + _SIDE_SLOTS


class PoolTooSmall(ValueError):
    """The pool the runtime was given cannot hold what the runtime may take."""


def pool_capacity(engine: AsyncEngine) -> int | None:
    """Connections ``engine`` can hand out at once; ``None`` when unbounded."""
    pool = engine.sync_engine.pool
    size = getattr(pool, "size", None)
    if not callable(size):
        return None  # NullPool / StaticPool: no ceiling to run into
    overflow = int(getattr(pool, "_max_overflow", 0))
    if overflow < 0:
        return None
    capacity = size()
    if not isinstance(capacity, int):
        return None
    return capacity + overflow


def check_fits(budget: DbBudget, engine: AsyncEngine | None) -> None:
    if engine is None:
        return
    capacity = pool_capacity(engine)
    if capacity is not None and capacity < budget.total:
        raise PoolTooSmall(
            f"the work runtime may hold {budget.total} connections "
            f"({dict(budget.handler)} for handlers, one per class to claim, "
            f"{budget.control} control, {_SIDE_SLOTS} side) but its pool holds "
            f"{capacity}; shrink WORK_DB_SLOTS_JSON / WORK_DB_CONTROL_SLOTS or "
            "give it a larger pool"
        )


def _semaphore(slots: int | None) -> asyncio.Semaphore | None:
    return None if slots is None else asyncio.Semaphore(slots)


class WorkDb:
    """Gated sessions for one runtime. ``budget=None`` gates nothing (tests, tools)."""

    def __init__(self, session_maker: SessionMaker, budget: DbBudget | None) -> None:
        self.session_maker = session_maker
        self.budget = budget
        handler = budget.handler if budget is not None else {}
        self._handler = {cls: _semaphore(n) for cls, n in handler.items()}
        self._dispatch = {cls: _semaphore(1) for cls in handler}
        self._control = _semaphore(budget.control if budget is not None else None)
        self._side = _semaphore(_SIDE_SLOTS if budget is not None else None)

    def handler(self, cls: WorkClass) -> AbstractSessionContext:
        return self._session(self._handler.get(cls), handler=True)

    def dispatch(self, cls: WorkClass) -> AbstractSessionContext:
        return self._session(self._dispatch.get(cls), handler=False)

    def control(self) -> AbstractSessionContext:
        return self._session(self._control, handler=False)

    @asynccontextmanager
    async def _session(
        self, gate: asyncio.Semaphore | None, *, handler: bool
    ) -> AsyncIterator[AsyncSession]:
        if handler and _in_handler_session.get():
            raise NestedWorkSession(
                "a work session is already open in this task; finish it before "
                "opening another (checkpoint and complete_work do not nest)"
            )
        async with _maybe(gate):
            side = _side_gate.set(self._side)
            nested = _in_handler_session.set(handler or _in_handler_session.get())
            try:
                async with self.session_maker() as session:
                    yield session
            finally:
                _in_handler_session.reset(nested)
                _side_gate.reset(side)


@asynccontextmanager
async def _maybe(gate: asyncio.Semaphore | None) -> AsyncIterator[None]:
    if gate is None:
        yield
        return
    async with gate:
        yield


@asynccontextmanager
async def side_connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A second connection beside the caller's, counted when a runtime session is open."""
    async with _maybe(_side_gate.get()):
        async with engine.connect() as connection:
            yield connection
