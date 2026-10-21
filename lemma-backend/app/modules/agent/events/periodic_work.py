"""The agent module's periodic work: two Agent Host checks.

* ``agent.host_dispatch_reconcile`` (5 min, automation) -- cancel host runs
  whose Lemma run already ended; advance lapsed leases.
* ``agent.host_retained_state_cleanup`` (1 d, admin) -- spent pairings,
  commands and leases.

Worker only (``register_periodic_work``).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkClass, periodic_job
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _uow():
    return SessionUnitOfWorkFactory(async_session_maker)()


# Automation, not admin: it pokes hosts over their links.
@periodic_job(
    "agent.host_dispatch_reconcile",
    every=timedelta(minutes=5),
    cls=WorkClass.AUTOMATION,
)
async def reconcile_agent_host_dispatch() -> None:
    """Reconcile Agent Host leases against the runs they belong to.

    Two things nobody else does once the worker driving a run is gone. Cancel
    host runs whose Lemma run already ended, so a machine on someone's desk
    stops executing tools for a turn we have reported as failed. And advance
    leases whose heartbeat lapsed, which otherwise stay non-terminal forever and
    are never collected by retention.
    """
    from app.modules.agent.infrastructure.agent_host import recovery
    from app.modules.agent.infrastructure.agent_host.channels import poke_host

    # Only database trouble is swallowed here: it is the transient failure this
    # sweep expects, and the next tick is five minutes away. Anything else is a
    # bug and surfaces through the worker's own job-failure path.
    # Two transactions, not one, and that is a deadlock fix rather than a
    # style choice. Both halves touch leases and commands; run together they
    # hold lease locks from `cancel_abandoned_host_runs` while
    # `reconcile_expired_leases` goes on to take command locks. A host's
    # `control` frame walks the same two tables the other way round -- commands
    # first, then a blocking lease lock -- so the two can wedge (ABBA), and
    # Postgres aborts one with 40P01. The link retries that once
    # (`AgentHostLinkStore.apply_control`); this removes the cause.
    #
    # Committing between them means no lease lock is ever held across a command
    # acquisition, which removes this side of the cycle. The two sweeps are
    # independent -- one cancels runs Lemma already finished, the other advances
    # lapsed leases -- so splitting them costs nothing but a second round trip
    # every five minutes.
    try:
        async with _uow() as uow:
            host_ids = await recovery.cancel_abandoned_host_runs(uow.session)
            await uow.commit()
        async with _uow() as uow:
            await recovery.reconcile_expired_leases(uow.session)
            await uow.commit()
    except SQLAlchemyError:
        logger.error(
            "agent.handlers.reconcile_agent_host_dispatch_cron.failed", exc_info=True
        )
        return
    # Poke outside the transaction: without it the cancel waits for the link's
    # 5-second floor instead of going out now. poke_host never raises.
    for host_id in dict.fromkeys(host_ids):
        await poke_host(host_id)


@periodic_job("agent.host_retained_state_cleanup", every=timedelta(days=1))
async def cleanup_agent_host_retained_state() -> None:
    """Collect spent Agent Host pairings, commands, and leases.

    Without this registration the sweep existed but never ran, so dispatch rows
    accumulated for the lifetime of the deployment and consumed pairing codes
    were never purged.
    """
    from app.modules.agent.infrastructure.agent_host import recovery

    try:
        async with _uow() as uow:
            await recovery.cleanup_retained_state(uow.session)
            await uow.commit()
    except SQLAlchemyError:
        logger.error(
            "agent.handlers.cleanup_agent_host_retained_state_cron.failed",
            exc_info=True,
        )
