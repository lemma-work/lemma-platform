"""Which surfaces an inbound event could possibly be for.

Every routing read starts from the same three facts -- the platform, that the
surface is ACTIVE, and that its pod still exists -- and then narrows by whatever
the event happens to carry. Kept together here because the narrowing is the part
that matters and the part that was missing: the shared statement is the
platform's entire surface list across the deployment, which each caller then
filtered in Python.

Beside the repository rather than inside it for the reason ``file_tree_sql`` is:
that file is at the architecture ratchet's per-file ceiling, and the reasoning
belongs somewhere it can be read as reasoning rather than as one more method.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Select, select

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfaceCredentialMode,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.pod.contracts.orm import Pod


#: A surface belongs to a pod, and a deleted pod has no business answering on
#: it. `PS-OPS-020` says deleting a pod stops the work it was doing and keeps it
#: stopped -- and a surface is the one piece of standing work that keeps running
#: without anybody in Lemma asking it to, because the trigger comes from
#: outside. The surface row itself stays ACTIVE on purpose: deletion is soft, so
#: nothing is rewritten and an undelete would restore a working surface. What
#: changes is that the pod is joined and checked here, once, rather than by each
#: of the ingress paths remembering to.
def in_a_live_pod():
    return (Pod.id == AgentSurface.pod_id) & (Pod.is_deleted.is_(False))


def active_surfaces_of_type(surface_type: str) -> Select:
    """Every live surface of one platform, in the order selection depends on.

    ``created_at, id`` is not cosmetic: it is the documented tiebreak when a
    sender resolves to several candidate surfaces on a shared bot or number, and
    it is what picks the identity surface. Every narrowing below inherits it.
    """
    return (
        select(AgentSurface)
        .where(
            AgentSurface.surface_type == surface_type,
            AgentSurface.status == AgentSurfaceStatus.ACTIVE.value,
        )
        .join(Pod, in_a_live_pod())
        .order_by(AgentSurface.created_at, AgentSurface.id)
    )


def routing_surfaces(
    surface_type: str,
    *,
    surface_ids: Collection[UUID] | None = None,
    external_workspace_id: str | None = None,
    system_credentials_only: bool = False,
) -> Select:
    """The live surfaces of one platform, narrowed by whatever the event carries.

    Each narrowing is a predicate that was already being applied -- in Python,
    after every surface of the platform in the deployment had been read and
    hydrated. Nothing here decides anything new; the same questions are asked of
    the database instead of the result.

    ``surface_ids`` is the native receiver's own list: Telegram polling and the
    Slack socket say which surfaces the bot that delivered this event serves,
    and without it a custom bot's update can be attributed to another bot's
    surface. An **empty** list still means "none of them", as it did in Python --
    ``IN ()`` matches nothing, which is the answer a receiver serving no surface
    should get.

    ``external_workspace_id`` is strict equality, matching
    ``AgentSurfaceEntity.matches_tenant``, and a NULL workspace does not match.
    That is deliberately not what ``matches_tenant`` says, where NULL means
    "any": the two ask different questions, and a surface that has not recorded
    a workspace is not in the one named here.

    ``system_credentials_only`` is the shared-webhook narrowing. A platform-wide
    webhook arrives on shared system credentials, so a surface bound to its own
    account cannot be what it is for -- and without the narrowing, continuity for
    the same external user or thread can pull a system-bot message into a
    custom-bot conversation. It is a flag rather than something derived here
    because it holds only for the platforms that have a shared bot at all; the
    caller owns that list, where it already lived.
    """
    statement = active_surfaces_of_type(surface_type)
    if surface_ids is not None:
        statement = statement.where(AgentSurface.id.in_(list(surface_ids)))
    if external_workspace_id:
        statement = statement.where(
            AgentSurface.external_workspace_id == external_workspace_id
        )
    if system_credentials_only:
        statement = statement.where(
            AgentSurface.account_id.is_(None),
            AgentSurface.credential_mode == SurfaceCredentialMode.SYSTEM.value,
        )
    return statement
