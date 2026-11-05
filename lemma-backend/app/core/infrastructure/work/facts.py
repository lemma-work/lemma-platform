"""Facts: how one module reacts to another without importing it.

A module announces a typed fact in the transaction that made it true; other
modules subscribe a work kind to it at wiring time. ``emit`` writes one work
item per matching subscriber into that same transaction. That is the whole
pub/sub system -- fan-out at write time from a static registry, so there is no
broker, no consumer group and no inbox, and a fact nobody subscribed to costs
nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from app.core.infrastructure.work.registry import (
    WorkRegistry,
    registry as default_registry,
)
from app.core.infrastructure.work.types import WorkClass
from app.core.infrastructure.work.writer import HasSession, WorkWriter


class Fact(BaseModel):
    """Something that became true. Carries ids, and names its namespace.

    Subclasses are the contract between modules: publish them from the
    emitting module's ``contracts`` package.
    """

    model_config = ConfigDict(frozen=True)

    def work_namespace(self) -> str:
        """The namespace subscriber work runs under ('pod:<id>', 'org:<id>', 'system')."""
        raise NotImplementedError(f"{type(self).__name__} must name its namespace")


@dataclass(frozen=True, slots=True)
class Subscription[F: Fact]:
    fact_type: type[F]
    kind: str
    cls: WorkClass | None
    when: Callable[[F], bool] | None
    dedup: Callable[[F], str] | None
    concurrency_key: Callable[[F], str] | None
    delay: timedelta | None

    def accepts(self, fact: F) -> bool:
        return self.when is None or self.when(fact)


class FactBus:
    def __init__(self, registry: WorkRegistry) -> None:
        self._registry = registry
        self._subscriptions: dict[type[Fact], list[Subscription[Fact]]] = {}

    def subscribe[F: Fact](
        self,
        fact_type: type[F],
        *,
        kind: str,
        cls: WorkClass | None = None,
        when: Callable[[F], bool] | None = None,
        dedup: Callable[[F], str] | None = None,
        concurrency_key: Callable[[F], str] | None = None,
        delay: timedelta | None = None,
    ) -> None:
        """Run ``kind`` with the fact as its payload whenever ``when(fact)`` holds.

        ``when`` must be a pure predicate on the fact -- it runs inside the
        emitter's transaction. ``cls``, when given, is checked against the
        kind's registration at emit time, so a subscriber that expects its
        kind in one class cannot silently land in another.
        """
        subscription = Subscription(
            fact_type, kind, cls, when, dedup, concurrency_key, delay
        )
        existing = self._subscriptions.setdefault(fact_type, [])
        if any(s.kind == kind for s in existing):
            raise ValueError(f"{kind!r} already subscribes to {fact_type.__name__}")
        # Stored widened: emit only ever hands a subscription facts of its own
        # type, which the dict key guarantees.
        existing.append(subscription)  # type: ignore[arg-type]

    def subscribers(self, fact_type: type[Fact]) -> list[Subscription[Fact]]:
        return list(self._subscriptions.get(fact_type, ()))

    async def emit(
        self,
        uow: HasSession,
        fact: Fact,
        *,
        writer: WorkWriter | None = None,
    ) -> int:
        """Write one item per matching subscriber; returns how many were enqueued."""
        work = writer or WorkWriter(uow.session, registry=self._registry)
        added = 0
        for subscription in self._subscriptions.get(type(fact), ()):
            if not subscription.accepts(fact):
                continue
            spec = self._registry.get(subscription.kind)
            if subscription.cls is not None and subscription.cls is not spec.cls:
                raise ValueError(
                    f"{subscription.kind!r} is registered in {spec.cls.value}, "
                    f"subscribed as {subscription.cls.value}"
                )
            item_id = await work.add(
                subscription.kind,
                fact.work_namespace(),
                fact,
                dedup_key=subscription.dedup(fact) if subscription.dedup else None,
                concurrency_key=(
                    subscription.concurrency_key(fact)
                    if subscription.concurrency_key
                    else None
                ),
                delay=subscription.delay,
            )
            added += item_id is not None
        return added


facts = FactBus(default_registry)
