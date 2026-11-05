"""What keeps a link to another pod in bounds: who stands behind it, how often.

A link lets one pod start runs in another with nobody present, so it holds only
while the person who made it could still make it, and only so many times an
hour. Both are checked as each ask over it opens (``pod_ask_service``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.authorization.context import ResourceRef
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.pod_asks import AskRefused
from app.modules.agent.infrastructure.pod_ask_queries import PodAskQueries
from app.modules.pod.contracts.members import pod_member_id

#: How many runs one pod's asks over a link may start in the pod it asks, per
#: window. A link is for a schedule's check or a task's lookup, not a loop: a
#: schedule every two minutes fits; one stuck re-asking does not, and the asked
#: pod's organization pays for every run.
LINK_RUNS_PER_WINDOW = 30
LINK_RUN_WINDOW = timedelta(hours=1)


async def refuse_past_link_cap(
    uow: SqlAlchemyUnitOfWork, *, to_pod_id: UUID, from_pod_id: UUID
) -> None:
    """Refuse an ask over a link that has started too many runs lately."""
    since = datetime.now(timezone.utc) - LINK_RUN_WINDOW
    started = await PodAskQueries(uow).link_runs_since(
        to_pod_id=to_pod_id, from_pod_id=from_pod_id, since=since
    )
    if started >= LINK_RUNS_PER_WINDOW:
        raise AskRefused(
            f"This pod has asked that one {started} times in the last hour, "
            "which is as many as a connection allows. Answer with what you "
            "have, and ask again later."
        )


async def still_stewards(
    uow: SqlAlchemyUnitOfWork,
    steward: UUID,
    *,
    answering_pod_id: UUID,
    asking_pod_id: UUID,
) -> bool:
    """Whether whoever connected the link could still connect it now.

    The same two tests connecting made: an admin of the pod being asked,
    and in the pod asking. Someone who has since left either, or stopped
    running the pod being asked, no longer stands behind what it shares.
    """
    if await pod_member_id(uow, asking_pod_id, steward) is None:
        return False
    if await pod_member_id(uow, answering_pod_id, steward) is None:
        return False
    context = await create_authorization_data_service(uow).build_user_context(
        user_id=steward, pod_id=answering_pod_id
    )
    return await context.can(
        Permissions.POD_MEMBER_MANAGE, ResourceRef.pod(answering_pod_id)
    )
