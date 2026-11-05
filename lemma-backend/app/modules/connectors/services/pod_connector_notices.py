"""Telling a pod's editors that a shared connector lost its account.

A SHARED connector runs as one member's account. When that member leaves the
pod (or the organization), the account leaves with them and the connector goes
NEEDS_ACCOUNT -- and with it every surface and schedule bound to it: a team
Slack bot stops answering, a trigger stops firing. Nothing said so. The people
who can fix it are the pod's editors (``pod.connector.manage``: they point the
connector at another shared account), so each is told once, in their own
channel, in the same transaction as the cleanup that caused it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger

logger = get_logger(__name__)


async def tell_editors_connectors_lost_their_account(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    departed_user_id: UUID,
    slugs: list[str],
) -> int:
    """Tell the pod's editors which SHARED connectors stopped. Returns how many."""
    if not slugs:
        return 0
    from app.modules.agent_surfaces.contracts.direct_messages import tell_member
    from app.modules.pod.contracts.members import pod_members_who_hold

    editors = [
        editor
        for editor in await pod_members_who_hold(
            uow, pod_id, Permissions.POD_CONNECTOR_MANAGE
        )
        if editor.user_id != departed_user_id
    ]
    named = ", ".join(f"'{slug}'" for slug in sorted(slugs))
    text = (
        f"The member whose account the shared connector{'s' if len(slugs) > 1 else ''} "
        f"{named} ran as has left this pod, so {'they have' if len(slugs) > 1 else 'it has'} "
        "no account now. Anything using them -- an agent, a team bot, a "
        "trigger -- has stopped. Point each at another account shared with the "
        "pod on its Connections page."
    )
    for editor in editors:
        await tell_member(
            uow,
            pod_id=pod_id,
            user_id=editor.user_id,
            text=text,
            # Once per departure: the cleanup job may be retried.
            key=f"connector-needs-account:{pod_id}:{departed_user_id}",
        )
    logger.info(
        "connectors.pod_connector.needs_account.told",
        pod_id=str(pod_id),
        connectors=len(slugs),
        editors_told=len(editors),
    )
    return len(editors)


__all__ = ["tell_editors_connectors_lost_their_account"]
