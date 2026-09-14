"""The reads behind "one surface may claim this", as statements.

Sibling of :mod:`surface_routing_sql`, and split from the repository for the
same reason: these are predicates, not plumbing, and the repository is already
at the size the architecture ratchet refuses to let grow.

They belong together because they are three answers to one question asked at
three distances. Two are scoped to an organization -- a connected account, and
the deployment's shared bot, are each somebody's to hold within their org. The
third is not scoped at all, because the thing it protects is the identity a
platform delivers to, and no platform has heard of our organizations.

``services.credential_uniqueness`` is where the refusals live; this is only
where the rows come from.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select

from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.surface_routing_sql import (
    in_a_live_pod,
)
from app.modules.pod.contracts.orm import Pod


def _org_of(pod_id: UUID):
    """The organization owning ``pod_id``, as a scalar subquery."""
    return select(Pod.organization_id).where(Pod.id == pod_id).scalar_subquery()


def _excluding(stmt: Select, exclude_surface_id: UUID | None) -> Select:
    """Drop one surface from the answer -- the row being written, on an update.

    Without it, re-saving a surface finds itself and refuses its own change.
    """
    if exclude_surface_id is None:
        return stmt
    return stmt.where(AgentSurface.id != exclude_surface_id)


def system_credential_conflict_in_org(
    *,
    pod_id: UUID,
    platform: str,
    exclude_surface_id: UUID | None = None,
) -> Select:
    """Another surface in this org already on the deployment's shared bot.

    ``account_id IS NULL`` is what makes it the *shared* one: a surface with an
    account of its own is not claiming the deployment's identity even when its
    credential mode says SYSTEM.
    """
    return _excluding(
        select(AgentSurface)
        .join(Pod, Pod.id == AgentSurface.pod_id)
        .where(
            Pod.organization_id == _org_of(pod_id),
            AgentSurface.surface_type == str(platform).upper(),
            AgentSurface.credential_mode == "SYSTEM",
            AgentSurface.account_id.is_(None),
        )
        .limit(1),
        exclude_surface_id,
    )


def account_conflict_in_org(
    *,
    pod_id: UUID,
    account_id: UUID,
    exclude_surface_id: UUID | None = None,
) -> Select:
    """Another surface in this org already bound to this connected account.

    Platform-blind on purpose: an account belongs to one connector, so naming
    the platform as well would only be a second way to say the same thing.
    """
    return _excluding(
        select(AgentSurface)
        .join(Pod, Pod.id == AgentSurface.pod_id)
        .where(
            Pod.organization_id == _org_of(pod_id),
            AgentSurface.account_id == account_id,
        )
        .limit(1),
        exclude_surface_id,
    )


def platform_identity_holder(
    *,
    pod_id: UUID,
    platform: str,
    external_workspace_id: str,
    surface_identity_id: str,
    exclude_surface_id: UUID | None = None,
) -> Select:
    """Whoever already answers as this bot, in any organization.

    The pair is the delivery key: which workspace, and which bot in it. Slack
    routes an event to the *app*, so two surfaces naming one bot are two rows
    the platform cannot tell apart -- wherever they sit.

    Selects ``same_org`` alongside the row rather than filtering on it, because
    the caller needs the distinction rather than one side of it: a holder in the
    reader's own organization is named in the refusal, and one outside it is
    not. ``created_at, id`` matches the routing tiebreak, so when rows written
    before this rule existed do collide, the one named here is the one that
    would have answered.

    Not narrowed to ACTIVE: a paused surface still holds its bot, and letting a
    second one take it would make resuming the first re-create the collision.
    Deleting the surface, or its pod, is what releases it.
    """
    return _excluding(
        select(
            AgentSurface,
            (Pod.organization_id == _org_of(pod_id)).label("same_org"),
        )
        .join(Pod, in_a_live_pod())
        .where(
            AgentSurface.surface_type == str(platform).upper(),
            AgentSurface.external_workspace_id == external_workspace_id,
            AgentSurface.surface_identity_id == surface_identity_id,
        )
        .order_by(AgentSurface.created_at, AgentSurface.id)
        .limit(1),
        exclude_surface_id,
    )
