"""Deleting many surfaces at once, without letting one failure strand the rest.

A function taking the service explicitly rather than a method on it, and rather
than an object of its own. The loop needs three things the service owns --
paging through a pod's surfaces, tearing one down, and the provider calls that
teardown makes -- so an object here would hold a reference to the service and
call back into it for everything it does, which is a mixin wearing a
constructor. That is the shape this module spent three passes removing.

What it is instead: the awkward part, in one place, named. Three callers want
"delete some of a pod's surfaces and do not give up half way" -- a pod being
deleted, an agent being deleted, and a pod giving back the scarce identities it
holds -- and they differ only in which subset they name.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger

logger = get_logger(__name__)


async def delete_matching_surfaces(
    service,
    pod_id: UUID,
    *,
    platform: str | None = None,
    agent_id: UUID | None = None,
    match_agent: bool = False,
    only_holding_an_identity: bool = False,
) -> int:
    """Delete a pod's surfaces, or the subset the filters name.

    Best-effort per surface. ``delete_surface`` runs the external teardown (a
    Telegram webhook, a Composio polling schedule) and deletes the row even when
    that fails, so an unreachable provider can neither keep a deleted agent's
    mailbox alive nor hold an org-unique account binding hostage. A failure here
    is counted and logged once at the end rather than raised, because the caller
    is usually a pod being deleted and stopping half way leaves a worse state
    than finishing: some surfaces gone, some alive, and no record of which.

    ``only_holding_an_identity`` narrows to surfaces that took something scarce
    -- a pooled WhatsApp number, an inbound address. One on the shared line has
    taken nothing, so releasing it early buys nothing and the ordinary
    pod-deleted teardown can have it.
    """
    deleted = 0
    failure_count = 0
    cursor: UUID | None = None
    while True:
        surfaces, cursor = await service.list_surfaces_by_pod(
            pod_id,
            platform=platform,
            agent_id=agent_id,
            match_agent=match_agent,
            cursor=cursor,
        )
        for surface in surfaces:
            if only_holding_an_identity and not surface.surface_identity_id:
                continue
            try:
                await service.delete_surface(surface.id)
                deleted += 1
            except Exception:
                # Each one, with its traceback. The count alone used to be the
                # only record, which said a teardown had gone wrong and nothing
                # about which surface or why -- and a provider that starts
                # refusing everything looked identical to one flaky row.
                failure_count += 1
                logger.warning(
                    "surface.cleanup.surface_failed.degraded",
                    pod_id=pod_id,
                    surface_id=surface.id,
                    exc_info=True,
                )
        if cursor is None:
            break
    if failure_count:
        logger.error(
            "surface.cleanup.failed", pod_id=pod_id, failure_count=failure_count
        )
    return deleted
