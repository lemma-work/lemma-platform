"""One dispatcher per class per process: reap expired leases, claim, run, repeat.

Slots are per process, so cluster capacity is replicas times slots. A round that
claims nothing waits for a NOTIFY, a freed slot, or the poll interval; a round
that claims something loops straight away, because there may be more.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from opentelemetry import metrics
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work.claim import ClaimRequest, claim
from app.core.infrastructure.work.execute import ExecEnv, capture, run_fast
from app.core.infrastructure.work.job import run_job
from app.core.infrastructure.work.rates import RateUse
from app.core.infrastructure.work.lifecycle import (
    apply_decision,
    expired_ids,
    lock_expired,
    owned_ids,
    release,
)
from app.core.infrastructure.work.retry import (
    DEFAULT_RETRY,
    AttemptState,
    Dead,
    after_lease_lost,
)
from app.core.infrastructure.work.types import ClaimedItem, WorkClass, WorkMode
from app.core.infrastructure.work.writer import waking_locally
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident
from app.core.request_context import create_background_task

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
#: Seconds, from a fast handler's milliseconds to an agent turn's hours: the
#: SDK's default buckets are sized for milliseconds and would put every job in
#: one bucket.
_SECONDS_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300,
    600, 1800, 3600, 7200, 14400,
)  # fmt: skip
claim_latency = meter.create_histogram(
    "lemma.work.claim_latency",
    unit="s",
    description="From an item becoming due (run_at) to its claim.",
    explicit_bucket_boundaries_advisory=_SECONDS_BUCKETS,
)
handler_duration = meter.create_histogram(
    "lemma.work.handler.duration",
    unit="s",
    description="Handler run time, by class, kind and outcome.",
    explicit_bucket_boundaries_advisory=_SECONDS_BUCKETS,
)
lease_reaped = meter.create_counter(
    "lemma.work.lease_reaped",
    description="Leases that expired under a dead or stalled process.",
)

_REAP_BATCH = 100
_REAP_INTERVAL_SECONDS = 1.0


class Dispatcher:
    def __init__(
        self,
        cls: WorkClass,
        env: ExecEnv,
        *,
        slots: int,
        quantum: int,
        poll_interval: float,
    ) -> None:
        self.cls = cls
        self.env = env
        self.slots = slots
        self.quantum = quantum
        self.poll_interval = poll_interval
        self._signal = asyncio.Event()
        self._running: set[asyncio.Task[str]] = set()
        self._last_reap = 0.0
        self._incident = DependencyIncident(
            f"work.dispatcher.{cls.value}", logger=logger
        )

    def wake(self) -> None:
        self._signal.set()

    @property
    def running(self) -> int:
        return len(self._running)

    def _leases(self) -> dict[str, timedelta]:
        registry = self.env.registry
        return {k: registry.get(k).lease for k in registry.kinds_for(self.cls)}

    def _rates(self) -> dict[str, tuple[RateUse, ...]]:
        registry = self.env.registry
        specs = (registry.get(k) for k in registry.kinds_for(self.cls))
        return {spec.kind: spec.rates for spec in specs if spec.rates}

    async def run(self) -> None:
        """Claim and start work until the environment says stop."""
        while not self.env.stopping.is_set():
            self._signal.clear()
            started = await self._round()
            if started == 0:
                await self._idle()

    async def _round(self) -> int:
        """Reap if due, then claim into free slots; returns how many started."""
        try:
            await self._reap_if_due()
            claimed = await self.claim_once()
        except (SQLAlchemyError, OSError) as exc:
            self._incident.record_failure(error_type=type(exc).__name__)
            return 0
        self._incident.record_success()
        for item in claimed:
            self._start(item)
        return len(claimed)

    async def _idle(self) -> None:
        try:
            await asyncio.wait_for(self._signal.wait(), timeout=self.poll_interval)
        except TimeoutError:
            pass  # the poll: what still delivers when a notification is lost

    async def claim_once(self) -> list[ClaimedItem]:
        free = self.slots - len(self._running)
        leases = self._leases()
        if free <= 0 or not leases:
            return []
        request = ClaimRequest(
            cls=self.cls,
            owner=self.env.owner,
            free=free,
            quantum=self.quantum,
            leases=leases,
            rates=self._rates(),
        )
        async with self.env.db.dispatch(self.cls) as session:
            claimed = await claim(session, request)
            await session.commit()
        now = self.env.clock()
        for item in claimed:
            latency = max(0.0, (now - item.run_at).total_seconds())
            claim_latency.record(latency, {"work_class": self.cls.value})
        return claimed

    def _start(self, item: ClaimedItem) -> None:
        task = create_background_task(self.execute(item), name=f"work-{item.kind}")
        self._running.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[str]) -> None:
        self._running.discard(task)
        self._signal.set()

    async def execute(self, item: ClaimedItem) -> str:
        """Run one claimed item to an outcome; never raises for a handler's failure."""
        spec = self.env.registry.get(item.kind)
        started = time.monotonic()
        outcome = "error"
        try:
            with waking_locally(self.env.local_wake):
                if spec.mode is WorkMode.FAST:
                    outcome = await run_fast(self.env, spec, item)
                else:
                    outcome = await run_job(self.env, spec, item)
        except SQLAlchemyError, OSError:
            # Settling itself failed -- the database went away mid-transition.
            # The item stays running under our lease, which will expire and be
            # reaped; nothing is lost, only delayed.
            logger.error(
                "work.item.settle_failed",
                item_id=str(item.id),
                kind=item.kind,
                ns=item.ns,
                exc_info=True,
            )
        finally:
            handler_duration.record(
                time.monotonic() - started,
                {
                    "work_class": self.cls.value,
                    "work_kind": item.kind,
                    "outcome": outcome,
                },
            )
        return outcome

    async def _reap_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_reap < _REAP_INTERVAL_SECONDS:
            return
        self._last_reap = now
        await self.reap()

    async def reap(self) -> int:
        """Return expired leases of this class to the queue, charging a lease loss each."""
        async with self.env.db.dispatch(self.cls) as session:
            ids = await expired_ids(session, self.cls.value, _REAP_BATCH)
        reaped = 0
        for item_id in ids:
            reaped += await self._reap_one(item_id)
        return reaped

    async def _reap_one(self, item_id: object) -> int:
        async with self.env.db.dispatch(self.cls) as session:
            uow = SqlAlchemyUnitOfWork(session)
            item = await lock_expired(session, item_id)
            if item is None:
                return 0
            spec = self.env.registry.find(item.kind)
            policy = spec.retry if spec is not None else DEFAULT_RETRY
            state = AttemptState(
                item.attempts, item.lease_losses, item.first_unavailable_at
            )
            decision = after_lease_lost(policy, state, now=self.env.clock())
            error = await capture(
                apply_decision(uow, item, decision, spec, self.env.registry)
            )
            if error is not None:
                logger.error(
                    "work.item.on_dead_failed",
                    item_id=str(item.id),
                    kind=item.kind,
                    ns=item.ns,
                    exc_info=error,
                )
                await uow.rollback()
                return 0
            await uow.commit()
        lease_reaped.add(1, {"work_class": self.cls.value, "work_kind": item.kind})
        logger.warning(
            "work.lease.reaped",
            item_id=str(item.id),
            kind=item.kind,
            ns=item.ns,
            lease_losses=decision.lease_losses,
            dead=isinstance(decision, Dead),
        )
        return 1

    async def drain(self, grace_seconds: float) -> int:
        """Shutdown: let running handlers finish within the grace, then release what is left.

        Released items go back to ``queued`` due now, with nothing charged, so
        the next process picks them up at once and a deploy never counts
        against an item.
        """
        if self._running:
            await asyncio.wait(set(self._running), timeout=grace_seconds)
        leftover = set(self._running)
        for task in leftover:
            task.cancel()
        if leftover:
            await asyncio.wait(leftover)
        return await self.release_owned()

    async def release_owned(self) -> int:
        async with self.env.db.dispatch(self.cls) as session:
            ids = await owned_ids(session, self.env.owner)
        released = 0
        for item_id in ids:
            async with self.env.db.dispatch(self.cls) as session:
                released += await release(
                    session, item_id, self.env.owner, now=self.env.clock()
                )
                await session.commit()
        if released:
            logger.info(
                "work.dispatcher.released", work_class=self.cls.value, released=released
            )
        return released
