"""What other modules may know about a pod's apps, in bulk.

The organization landing page needs every pod's apps at once. Asking per pod
would make the page cost one query per pod, which is the shape this exists to
avoid: one query answers for the whole set.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.apps.domain.entities import public_app_url
from app.modules.apps.infrastructure.models import AppModel


@dataclass(frozen=True, slots=True)
class PodAppSummary:
    """An app as a listing entry: enough to show it and to open it."""

    id: UUID
    name: str
    description: str | None
    # None where the deployment serves no app host -- see `public_app_url`.
    url: str | None
    status: str


async def list_app_summaries_by_pod(
    *,
    session,
    pod_ids: list[UUID],
) -> dict[UUID, list[PodAppSummary]]:
    """Apps for every given pod, keyed by pod, in one query.

    Pods with no apps are absent rather than mapped to an empty list; callers
    read through ``.get(pod_id, [])`` so the difference never reaches a response.
    """
    if not pod_ids:
        return {}
    rows = (
        await session.execute(
            select(
                AppModel.id,
                AppModel.pod_id,
                AppModel.name,
                AppModel.description,
                AppModel.public_slug,
                AppModel.status,
            )
            .where(AppModel.pod_id.in_(pod_ids))
            .order_by(AppModel.name)
        )
    ).all()

    summaries: dict[UUID, list[PodAppSummary]] = defaultdict(list)
    for app_id, pod_id, name, description, public_slug, app_status in rows:
        summaries[pod_id].append(
            PodAppSummary(
                id=app_id,
                name=name,
                description=description,
                url=public_app_url(public_slug),
                status=str(app_status),
            )
        )
    return dict(summaries)


async def list_readable_app_summaries(
    *,
    session,
    pod_id: UUID,
    ctx: Context,
) -> list[PodAppSummary]:
    """One pod's apps, filtered to what this context may actually read.

    Separate from the bulk listing above rather than an optional argument on
    it. The bulk one answers for the organization landing page, which does its
    own authorization upstream and wants every pod at once; this one answers for
    an agent's runtime brief, where the reader is one user and an app carries
    its own visibility and owner. An optional ``ctx`` would be the version a
    caller forgets to pass.
    """
    actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.APP,
        resource_id_col=AppModel.id,
        pod_id_col=AppModel.pod_id,
        owner_user_id_col=AppModel.user_id,
        visibility_col=AppModel.visibility,
    )
    rows = (
        await session.execute(
            select(
                AppModel.id,
                AppModel.name,
                AppModel.description,
                AppModel.public_slug,
                AppModel.status,
            )
            .where(
                AppModel.pod_id == pod_id,
                allowed_actions_contains(actions, Permissions.APP_READ),
            )
            .order_by(AppModel.name)
        )
    ).all()
    return [
        PodAppSummary(
            id=app_id,
            name=name,
            description=description,
            url=public_app_url(public_slug),
            status=str(app_status),
        )
        for app_id, name, description, public_slug, app_status in rows
    ]
