"""``surface.receiver``: one perpetual, leased item per native receiver credential.

Telegram long-polling, Slack Socket Mode, Resend polling and the Telegram
manager bot each run as one work item in the ``receivers`` class -- never an
``interactive`` slot an agent turn needs. The item's lease *is* the receiver's
lease: one worker holds it and heartbeats it; if that worker dies, the lease
lapses and another worker claims the item and resumes from the cursor in
``surface_receiver_state``. That replaced the Redis lease each worker's
coordinator took per credential, and gives the Telegram manager bot a lease
for the first time (every flagged worker used to poll it).

The handler runs the receiver until one of:

* the process stops -> ``Released`` (requeued, nothing charged);
* the credential is no longer wanted (surface removed, mode switched off) ->
  the item completes, and the reconciler does not add it again;
* upstream says another consumer owns the credential (a Telegram 409 past its
  grace, a revoked token) -> ``Defer`` for the cooldown, so the working
  consumer is left alone and this one comes back without anybody acting;
* the receiver crashed -> ``Defer`` a little, nothing charged.

``surfaces.receivers.reconcile`` (periodic, every 15 s) adds the item for every
wanted credential that has none and drops queued ones nobody wants.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    Defer,
    JobContext,
    Released,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    periodic,
    pod_namespace,
    registry as default_registry,
)
from app.core.infrastructure.work.models import WorkItem
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.work_payloads import NoPayload, ReceiverPayload
from app.modules.agent_surfaces.events.work_kinds import (
    RECEIVERS_RECONCILE_PERIODIC,
    SURFACE_RECEIVER,
    receiver_key,
)
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    NativeReceiverConflict,
    NativeReceiverCredentialsRejected,
    ReceiverInbox,
    ReceiverRunner,
    ReceiverRunnerFactory,
)
from app.modules.agent_surfaces.services.native_receiver_candidates import (
    load_receiver_candidates,
)
from app.modules.agent_surfaces.services.telegram_manager_receiver import (
    TELEGRAM_MANAGER_RECEIVER_KEY,
)

logger = get_logger(__name__)

#: The reconciler's kind: the periodic of the same name runs it.
RECEIVERS_RECONCILE = RECEIVERS_RECONCILE_PERIODIC
_RECONCILE_EVERY = timedelta(seconds=15)
#: How long a receiver stands down after upstream says the credential is
#: somebody else's. Long enough that the working consumer is left alone rather
#: than interrupted every scan, short enough that this one takes over if that
#: consumer is switched off.
CONFLICT_COOLDOWN = timedelta(minutes=10)
#: After a crash: back soon, nothing charged.
CRASH_BACKOFF = timedelta(seconds=30)
#: How often a running receiver asks whether it is still wanted.
_WANTED_CHECK_SECONDS = 30.0

#: A perpetual item: neither attempts nor lease losses may ever retire it.
#: More native receivers than any deployment runs; the reconcile reads at most this.
_MAX_RECEIVERS = 1000

_PERPETUAL = RetryPolicy(max_attempts=1_000_000, max_lease_losses=1_000_000)

type ManagerFactory = Callable[[UnitOfWorkFactory], ReceiverRunner]


def _default_factories() -> dict[SurfacePlatform, ReceiverRunnerFactory]:
    from app.modules.agent_surfaces.services.resend_polling_receiver import (
        ResendPollingReceiverRunner,
    )
    from app.modules.agent_surfaces.services.slack_socket_runner import (
        SlackSocketReceiverRunner,
    )
    from app.modules.agent_surfaces.services.telegram_polling_runner import (
        TelegramPollingReceiverRunner,
    )

    return {
        SurfacePlatform.TELEGRAM: TelegramPollingReceiverRunner,
        SurfacePlatform.SLACK: SlackSocketReceiverRunner,
        SurfacePlatform.RESEND: ResendPollingReceiverRunner,
    }


def _default_manager(uow_factory: UnitOfWorkFactory) -> ReceiverRunner:
    from app.modules.agent_surfaces.services.telegram_manager_receiver import (
        TelegramManagerPollingReceiver,
    )

    return TelegramManagerPollingReceiver(uow_factory=uow_factory)


def manager_receiver_wanted() -> bool:
    from app.modules.agent_surfaces.services.telegram_manager_receiver import (
        TelegramManagerPollingReceiver,
    )

    if not surface_settings.enable_telegram_manager_polling_mode:
        return False
    return TelegramManagerPollingReceiver().should_start()


async def wanted_receivers(
    uow_factory: UnitOfWorkFactory,
    *,
    manager_wanted: Callable[[], bool] = manager_receiver_wanted,
) -> dict[str, str]:
    """``{receiver key: namespace}`` for every receiver this deployment wants."""
    async with uow_factory() as uow:
        candidates = await load_receiver_candidates(uow)
    wanted = {
        candidate.key: (
            pod_namespace(candidate.pod_id) if candidate.pod_id else SYSTEM_NAMESPACE
        )
        for candidate in candidates
    }
    if manager_wanted():
        wanted[TELEGRAM_MANAGER_RECEIVER_KEY] = SYSTEM_NAMESPACE
    return wanted


async def _candidate(
    uow_factory: UnitOfWorkFactory, key: str
) -> NativeReceiverCandidate | None:
    async with uow_factory() as uow:
        for candidate in await load_receiver_candidates(uow):
            if candidate.key == key:
                return candidate
    return None


async def _until_unwanted(still_wanted: Callable[[], Awaitable[bool]]) -> None:
    while True:
        await asyncio.sleep(_WANTED_CHECK_SECONDS)
        try:
            if not await still_wanted():
                return
        except Exception:
            # A failed check is not an answer: keep receiving.
            logger.warning(
                "agent_surfaces.receiver_work.wanted_check_failed.degraded",
                exc_info=True,
            )


async def supervise(
    ctx: JobContext,
    receive: Coroutine[Any, Any, None],
    *,
    still_wanted: Callable[[], Awaitable[bool]],
    label: str,
    platform: str,
) -> None:
    """Run ``receive`` until stop, unwanted, conflict or crash -- see the module."""
    runner = create_inherited_task(receive, name=f"surface-receiver-{label}")
    stopping = create_inherited_task(ctx.until_stopping(), name="receiver-stop")
    watch = create_inherited_task(_until_unwanted(still_wanted), name="receiver-watch")
    tasks = {runner, stopping, watch}
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if runner in done and not runner.cancelled():
        failure = runner.exception()
        if isinstance(failure, NativeReceiverCredentialsRejected):
            logger.warning(
                "agent_surfaces.event_receiver_service.native_receiver_credentials_rejected.degraded",
                platform=platform,
                credential_label=label,
                cooldown_seconds=CONFLICT_COOLDOWN.total_seconds(),
                reason=str(failure),
            )
            raise Defer(CONFLICT_COOLDOWN)
        if isinstance(failure, NativeReceiverConflict):
            # Loud, at a level that survives LOG_LEVEL=INFO: a bot shared between
            # two installations otherwise looks like an unreliable bot.
            logger.warning(
                "agent_surfaces.event_receiver_service.native_receiver_upstream_conflict.degraded",
                platform=platform,
                credential_label=label,
                cooldown_seconds=CONFLICT_COOLDOWN.total_seconds(),
                reason=str(failure),
            )
            raise Defer(CONFLICT_COOLDOWN)
        if failure is not None:
            logger.warning(
                "agent_surfaces.event_receiver_service.native_receiver_stopped.failed",
                platform=platform,
                credential_label=label,
                exc_info=failure,
            )
            raise Defer(CRASH_BACKOFF)
        # Returned on its own: nothing to run on (no token). Look again later.
        raise Defer(CONFLICT_COOLDOWN)
    if stopping in done:
        raise Released()
    logger.info(
        "agent_surfaces.receiver_work.receiver_retired.observed",
        platform=platform,
        credential_label=label,
    )


def make_receiver(
    *,
    uow_factory: UnitOfWorkFactory | None = None,
    factories: dict[SurfacePlatform, ReceiverRunnerFactory] | None = None,
    manager: ManagerFactory | None = None,
    manager_wanted: Callable[[], bool] = manager_receiver_wanted,
):
    async def run(ctx: JobContext, payload: ReceiverPayload) -> None:
        sessions = uow_factory or SessionUnitOfWorkFactory(async_session_maker)
        key = payload.receiver_key
        if key == TELEGRAM_MANAGER_RECEIVER_KEY:
            if not manager_wanted():
                return

            async def manager_still_wanted() -> bool:
                return manager_wanted()

            await supervise(
                ctx,
                (manager or _default_manager)(sessions).run(),
                still_wanted=manager_still_wanted,
                label="telegram-manager",
                platform="TELEGRAM",
            )
            return
        candidate = await _candidate(sessions, key)
        if candidate is None:
            return
        runner_factory = (factories or _default_factories()).get(candidate.platform)
        if runner_factory is None:
            return

        async def still_wanted() -> bool:
            return await _candidate(sessions, key) is not None

        await supervise(
            ctx,
            runner_factory(
                candidate, ReceiverInbox(candidate, uow_factory=sessions)
            ).run(),
            still_wanted=still_wanted,
            label=candidate.credential_label,
            platform=candidate.platform.value,
        )

    return run


def make_reconcile(
    *,
    uow_factory: UnitOfWorkFactory | None = None,
    manager_wanted: Callable[[], bool] = manager_receiver_wanted,
):
    async def reconcile(ctx: JobContext, payload: NoPayload) -> None:
        del payload
        sessions = uow_factory or SessionUnitOfWorkFactory(async_session_maker)
        wanted = await wanted_receivers(sessions, manager_wanted=manager_wanted)
        async with ctx.complete_work() as uow:
            await reconcile_receivers(ctx.writer(uow), wanted, session=uow.session)

    return reconcile


async def reconcile_receivers(
    work: WorkWriter, wanted: dict[str, str], *, session: Any
) -> None:
    """One live item per wanted receiver; queued ones nobody wants are dropped.

    A running receiver that is no longer wanted notices on its own and
    completes. A dead one (it should never die) is replaced.
    """
    rows = await session.execute(
        select(WorkItem.dedup_key, WorkItem.state, WorkItem.ns, WorkItem.payload)
        .where(WorkItem.kind == SURFACE_RECEIVER)
        # One per credential: a deployment has a handful.
        .limit(_MAX_RECEIVERS)
    )
    live: dict[str, str] = {}
    for dedup, state, ns, item_payload in rows.all():
        key = str((item_payload or {}).get("receiver_key") or "")
        if state in ("queued", "running"):
            live[key] = ns
            if state == "queued" and key not in wanted and dedup:
                await work.cancel(SURFACE_RECEIVER, ns, dedup)
    for key, ns in wanted.items():
        if key in live:
            continue
        platform = (
            "TELEGRAM" if key == TELEGRAM_MANAGER_RECEIVER_KEY else key.split(":", 1)[0]
        )
        await work.add(
            SURFACE_RECEIVER,
            ns,
            ReceiverPayload(receiver_key=key, platform=platform.upper()),
            dedup_key=receiver_key(key),
        )
        logger.info(
            "agent_surfaces.receiver_work.receiver_started.observed",
            platform=platform.upper(),
        )


async def notify_surface_receiver_config_changed(surface_id: object = None) -> None:
    """Reconcile now, after a surface was created, changed or removed.

    The periodic would find it within 15 s anyway; this is the difference
    between a receiver starting now and in a few seconds. Never raises.
    """
    del surface_id
    try:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            await WorkWriter.of(uow).add(
                RECEIVERS_RECONCILE,
                SYSTEM_NAMESPACE,
                NoPayload(),
                dedup_key="receivers-reconcile-now",
            )
    except Exception:
        logger.warning(
            "agent_surfaces.event_receiver_service.receiver_wakeup_publish_failed.degraded",
            exc_info=True,
        )


def register_receiver_work(
    registry: WorkRegistry,
    *,
    uow_factory: UnitOfWorkFactory | None = None,
    factories: dict[SurfacePlatform, ReceiverRunnerFactory] | None = None,
    manager: ManagerFactory | None = None,
    manager_wanted: Callable[[], bool] = manager_receiver_wanted,
    lease: timedelta | None = None,
) -> None:
    registry.handler(
        kind=SURFACE_RECEIVER,
        cls=WorkClass.RECEIVERS,
        mode=WorkMode.JOB,
        payload=ReceiverPayload,
        retry=_PERPETUAL,
        lease=lease,
    )(
        make_receiver(
            uow_factory=uow_factory,
            factories=factories,
            manager=manager,
            manager_wanted=manager_wanted,
        )
    )
    registry.handler(
        kind=RECEIVERS_RECONCILE,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=NoPayload,
    )(make_reconcile(uow_factory=uow_factory, manager_wanted=manager_wanted))


register_receiver_work(default_registry)
periodic(RECEIVERS_RECONCILE_PERIODIC, _RECONCILE_EVERY, RECEIVERS_RECONCILE)
