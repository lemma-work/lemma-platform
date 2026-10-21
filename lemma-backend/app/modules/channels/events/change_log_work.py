"""``channels.changes_prune`` -- every hour, the channels' change log forgets
what is older than ``revisions.CHANGE_RETENTION``.

The log is what a live subscriber resumes from; one resuming from before what
is kept is told to refetch instead (``resync``). A batch per transaction, a
few batches per run: a backlog is worked off over the following hours.
"""

from __future__ import annotations

from datetime import timedelta

from opentelemetry import metrics

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work.periodic_jobs import periodic_job
from app.core.log.log import get_logger
from app.modules.channels.infrastructure.revisions import prune_changes

logger = get_logger(__name__)
changes_pruned = metrics.get_meter(__name__).create_counter(
    "lemma.channels.changes_pruned",
    description="Rows of the channels' change log forgotten for their age.",
)

CHANGES_PRUNE = "channels.changes_prune"

#: One transaction's deletes, and the most transactions in one run.
_BATCH = 5000
_BATCHES = 20


@periodic_job(CHANGES_PRUNE, every=timedelta(hours=1))
async def changes_prune() -> None:
    pruned = 0
    for _ in range(_BATCHES):
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            batch = await prune_changes(uow.session, limit=_BATCH)
            await uow.commit()
        pruned += batch
        if batch < _BATCH:
            break
    if pruned:
        changes_pruned.add(pruned)
        logger.info(
            "channels.changes.pruned.observed",
            count=pruned,
            backlog=pruned >= _BATCH * _BATCHES,
        )
