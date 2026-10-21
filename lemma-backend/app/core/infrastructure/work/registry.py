"""Handler and periodic registrations: what each kind is and how it runs.

A kind's class, retry policy, concurrency limit and lease live here, not on the
enqueue call, so a writer cannot put a kind in the wrong class and two writers
cannot disagree about its limits. The registry is filled at import time by
``@work_handler``; a dispatcher only claims kinds registered in its own process,
so an old worker in a rolling deploy never claims a kind it cannot run.
"""

from __future__ import annotations

import zlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, overload

from pydantic import BaseModel

from app.core.infrastructure.work.config import CLASS_DEFAULTS
from app.core.infrastructure.work.fast_guard import (
    check_fast_module_namespace,
    check_handler_signature,
)
from app.core.infrastructure.work.rates import RateUse, is_known_rate
from app.core.infrastructure.work.retry import DEFAULT_RETRY, RetryPolicy
from app.core.infrastructure.work.types import JsonObject, WorkClass, WorkMode

if TYPE_CHECKING:
    from app.core.infrastructure.work.context import DeadContext, FastContext
    from app.core.infrastructure.work.job import JobContext

type FastHandler[P: BaseModel] = Callable[["FastContext", P], Awaitable[None]]
type JobHandler[P: BaseModel] = Callable[["JobContext", P], Awaitable[None]]
type DeadHook = Callable[["DeadContext"], Awaitable[None]]

PERIODIC_PREFIX = "periodic:"


class UnknownWorkKind(LookupError):
    """No handler for this kind is registered in this process."""


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    kind: str
    cls: WorkClass
    mode: WorkMode
    payload_type: type[BaseModel]
    #: The decorated coroutine function. Its context type follows ``mode``.
    handler: Callable[..., Awaitable[None]]
    retry: RetryPolicy
    lease: timedelta
    #: Max running items sharing one concurrency key; None = keys not allowed.
    concurrency: int | None
    on_dead: DeadHook | None
    priority: int
    #: Buckets each item draws one token from at claim. Empty = unlimited.
    rates: tuple[RateUse, ...] = ()
    #: Wall-clock ceiling on one run of a JOB; past it the job is cancelled
    #: and goes dead (``max_runtime``). None = bounded only by the handler.
    max_runtime: timedelta | None = None

    @property
    def rate_per_key(self) -> bool:
        return any(use.per_key for use in self.rates)


@dataclass(frozen=True, slots=True)
class PeriodicSpec:
    name: str
    every: timedelta
    kind: str
    payload: JsonObject = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{PERIODIC_PREFIX}{self.name}"

    @property
    def phase(self) -> timedelta:
        """How long after arming the first run is due: stable per name.

        Every periodic is armed at the same worker boot and re-arms itself
        ``every`` after each run, so without an offset all the hourly sweeps
        would fire together forever -- the convoy that showed up as event-loop
        stalls on the round minute when they were crons. A hash of the name
        spreads them across their interval (capped at an hour, so a daily
        sweep still runs soon after a fresh deploy).
        """
        window = int(min(self.every, timedelta(hours=1)).total_seconds())
        return timedelta(seconds=zlib.crc32(self.name.encode()) % max(window, 1))


class WorkRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerSpec] = {}
        self._periodics: dict[str, PeriodicSpec] = {}

    def register(self, spec: HandlerSpec) -> None:
        existing = self._handlers.get(spec.kind)
        if existing is not None and existing.handler is not spec.handler:
            raise ValueError(f"work kind {spec.kind!r} is already registered")
        if spec.concurrency is not None and spec.concurrency < 1:
            raise ValueError(f"work kind {spec.kind!r}: concurrency must be >= 1")
        _check_max_runtime(spec)
        _check_rates(spec)
        if spec.mode is WorkMode.FAST:
            check_handler_signature(spec.kind, spec.handler, "FastContext")
            check_fast_module_namespace(spec.kind, spec.handler)
        else:
            check_handler_signature(spec.kind, spec.handler, "JobContext")
        self._handlers[spec.kind] = spec

    @overload
    def handler[P: BaseModel](
        self,
        *,
        kind: str,
        cls: WorkClass,
        mode: Literal[WorkMode.FAST],
        payload: type[P],
        retry: RetryPolicy = ...,
        lease: timedelta | None = ...,
        concurrency: int | None = ...,
        on_dead: DeadHook | None = ...,
        priority: int = ...,
        rates: Sequence[RateUse | str] = ...,
        max_runtime: None = ...,
    ) -> Callable[[FastHandler[P]], FastHandler[P]]: ...

    @overload
    def handler[P: BaseModel](
        self,
        *,
        kind: str,
        cls: WorkClass,
        mode: Literal[WorkMode.JOB],
        payload: type[P],
        retry: RetryPolicy = ...,
        lease: timedelta | None = ...,
        concurrency: int | None = ...,
        on_dead: DeadHook | None = ...,
        priority: int = ...,
        rates: Sequence[RateUse | str] = ...,
        max_runtime: timedelta | None = ...,
    ) -> Callable[[JobHandler[P]], JobHandler[P]]: ...

    def handler[P: BaseModel](
        self,
        *,
        kind: str,
        cls: WorkClass,
        mode: WorkMode,
        payload: type[P],
        retry: RetryPolicy = DEFAULT_RETRY,
        lease: timedelta | None = None,
        concurrency: int | None = None,
        on_dead: DeadHook | None = None,
        priority: int = 0,
        rates: Sequence[RateUse | str] = (),
        max_runtime: timedelta | None = None,
    ) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
        """Register the decorated coroutine as the handler for ``kind``.

        ``rates`` names the buckets each item draws a token from when claimed
        (a bare string is a per-namespace bucket); an item over a rate waits
        for its token, it is never charged or dropped.

        ``max_runtime`` (JOB only) caps one run's wall-clock time: past it the
        job is cancelled and the item goes dead with reason ``max_runtime``,
        so ``on_dead`` records the terminal state. A handler that wants to end
        on its own terms first reads ``ctx.deadline``.
        """
        uses = tuple(RateUse(use) if isinstance(use, str) else use for use in rates)

        def decorate(
            fn: Callable[..., Awaitable[None]],
        ) -> Callable[..., Awaitable[None]]:
            self.register(
                HandlerSpec(
                    kind=kind,
                    cls=cls,
                    mode=mode,
                    payload_type=payload,
                    handler=fn,
                    retry=retry,
                    lease=lease or CLASS_DEFAULTS[cls].lease,
                    concurrency=concurrency,
                    on_dead=on_dead,
                    priority=priority,
                    rates=uses,
                    max_runtime=max_runtime,
                )
            )
            return fn

        return decorate

    def periodic(
        self,
        name: str,
        every: timedelta,
        kind: str,
        payload: JsonObject | None = None,
    ) -> PeriodicSpec:
        """Run ``kind`` every ``every``, starting at the next worker boot.

        The kind must be registered (now or before boot). Each run enqueues the
        next in its own completion transaction; a periodic that goes dead stops
        until the next boot recreates it, and says so in the log.
        """
        if every <= timedelta(0):
            raise ValueError("periodic interval must be positive")
        spec = PeriodicSpec(name, every, kind, dict(payload or {}))
        existing = self._periodics.get(name)
        if existing is not None and existing != spec:
            raise ValueError(f"periodic {name!r} is already registered differently")
        self._periodics[name] = spec
        return spec

    def get(self, kind: str) -> HandlerSpec:
        try:
            return self._handlers[kind]
        except KeyError:
            raise UnknownWorkKind(kind) from None

    def find(self, kind: str) -> HandlerSpec | None:
        return self._handlers.get(kind)

    def kinds_for(self, cls: WorkClass) -> list[str]:
        return sorted(k for k, spec in self._handlers.items() if spec.cls is cls)

    def periodics(self) -> list[PeriodicSpec]:
        return list(self._periodics.values())

    def periodic_for(self, dedup_key: str | None) -> PeriodicSpec | None:
        if dedup_key is None or not dedup_key.startswith(PERIODIC_PREFIX):
            return None
        return self._periodics.get(dedup_key.removeprefix(PERIODIC_PREFIX))


def _check_max_runtime(spec: HandlerSpec) -> None:
    if spec.max_runtime is None:
        return
    if spec.mode is WorkMode.FAST:
        # A fast handler is bounded by its statement timeout; a second clock
        # would only race it.
        raise ValueError(f"work kind {spec.kind!r}: max_runtime is for JOB kinds")
    if spec.max_runtime <= timedelta(0):
        raise ValueError(f"work kind {spec.kind!r}: max_runtime must be positive")


def _check_rates(spec: HandlerSpec) -> None:
    names = [use.name for use in spec.rates]
    if len(set(names)) != len(names):
        raise ValueError(f"work kind {spec.kind!r} names a rate twice")
    for name in names:
        if not is_known_rate(name):
            # A rate with no default would be unlimited until some override
            # happened to name it -- a limit that silently does not apply.
            raise ValueError(
                f"work kind {spec.kind!r}: no default for rate {name!r}; add it "
                "to DEFAULT_RATES or WORK_RATES_JSON"
            )


#: The process's registry, filled by ``@work_handler`` at import time.
registry = WorkRegistry()
work_handler = registry.handler


def periodic(
    name: str, every: timedelta, kind: str, payload: JsonObject | None = None
) -> PeriodicSpec:
    return registry.periodic(name, every, kind, payload)
