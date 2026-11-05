"""Two contacts turned out to be one: their rows follow the survivor.

A web visitor who writes a row and later proves an email an older contact
already holds is merged into that contact (``contacts.services.proofs``). The
contacts module moves its handles and sessions itself and announces
``ContactMerged``; this moves the rows the merged contact wrote in the pod's
contact-owned tables, so the person the pod now knows reads them, and
forgetting that person deletes them.

A JOB in the ``events`` class because it writes the datastore database.
Idempotent: a retry finds nothing left naming the merged contact.
"""

from __future__ import annotations

from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.infrastructure.work import (
    JobContext,
    Unavailable,
    UnavailableEvidence,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.contacts.contracts.facts import ContactMerged

DATASTORE_CONTACT_MERGED = "datastore.contact_merged"


async def _survivor_now(payload: ContactMerged):
    """Whoever the payload's survivor is now: merged again since, it is the
    contact that one became. ``None`` once nobody is left to own the rows."""
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.contacts.contracts import contact_by_id

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await contact_by_id(
            uow, pod_id=payload.pod_id, contact_id=payload.survivor_id
        )


async def reassign_rows(ctx: JobContext, payload: ContactMerged) -> None:
    from app.modules.datastore.contracts.contact_rows import reassign_contact_rows

    survivor = await _survivor_now(payload)
    if survivor is None:
        return
    try:
        await reassign_contact_rows(
            ctx.checkpoint,
            pod_id=payload.pod_id,
            merged_id=payload.merged_id,
            survivor_id=survivor.id,
        )
    except (OSError, OperationalError, InterfaceError) as exc:
        raise Unavailable(
            f"datastore database unreachable: {type(exc).__name__}",
            evidence=UnavailableEvidence.CONNECTION_REFUSED,
        ) from exc


def register_contact_merged_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=DATASTORE_CONTACT_MERGED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ContactMerged,
    )(reassign_rows)


register_contact_merged_work(default_registry)
facts.subscribe(
    ContactMerged,
    kind=DATASTORE_CONTACT_MERGED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"datastore-contact-merged:{fact.merged_id}",
)
