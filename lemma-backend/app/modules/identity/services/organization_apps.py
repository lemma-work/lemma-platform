"""Every app a member may open across an organization's pods, newest first.

A pod's app list answered this per pod, and the organization's overview capped
each at twelve to keep the fan-out tolerable -- so the thirteenth app of a busy
pod never showed anywhere. One query here for every pod, each pod's apps
filtered by that pod's own context: an app carries its own visibility and
owner, so being in the pod is not the same as being allowed to open it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.apps.contracts.pod_summaries import (
    ReadablePodApp,
    list_readable_apps_by_pod,
)
from app.modules.identity.services.organization_scope import load_member_scope

#: The most apps one pod contributes: a ceiling no ordinary pod reaches, not a
#: page, so that one pod cannot make the read grow without bound.
APPS_PER_POD = 200


@dataclass(frozen=True, slots=True)
class OrganizationApp:
    pod_id: UUID
    pod_name: str
    app: ReadablePodApp


async def load_organization_apps(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID
) -> list[OrganizationApp] | None:
    """The member's apps across the organization. None for a non-member."""
    scope = await load_member_scope(
        session=session, user_id=user_id, organization_id=organization_id
    )
    if scope is None:
        return None
    by_pod = await list_readable_apps_by_pod(
        session=session, contexts=scope.contexts, limit=APPS_PER_POD
    )
    names = scope.pod_names
    listed = [
        OrganizationApp(pod_id=pod_id, pod_name=names.get(pod_id, ""), app=app)
        for pod_id, apps in by_pod.items()
        for app in apps
    ]
    return sorted(
        listed, key=lambda each: (each.app.updated_at, each.app.id), reverse=True
    )
