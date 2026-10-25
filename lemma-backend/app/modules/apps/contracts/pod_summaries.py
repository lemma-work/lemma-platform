"""What other modules may know about a pod's apps, in bulk.

The organization landing page needs every pod's apps at once. Asking per pod
would make the page cost one query per pod, which is the shape this exists to
avoid: one query answers for the whole set.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Subquery, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    public_slug: str


@dataclass(frozen=True, slots=True)
class ReadablePodApp:
    """An app as a list across pods shows it: who may see it, and how fresh."""

    id: UUID
    pod_id: UUID
    name: str
    description: str | None
    # None where the deployment serves no app host -- see `public_app_url`.
    url: str | None
    status: str
    visibility: str
    updated_at: datetime


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
                public_slug=public_slug,
            )
        )
    return dict(summaries)


async def list_readable_app_summaries(
    *,
    session,
    pod_id: UUID,
    ctx: Context,
    limit: int,
) -> list[PodAppSummary]:
    """One pod's apps, filtered to what this context may actually read.

    Separate from the bulk listing above rather than an optional argument on
    it. The bulk one answers for the organization landing page, which does its
    own authorization upstream and wants every pod at once; this one answers for
    an agent's runtime brief, where the reader is one user and an app carries
    its own visibility and owner. An optional ``ctx`` would be the version a
    caller forgets to pass.

    ``limit`` is required for the same reason. The brief caps what it renders,
    but capping the render is not capping the read -- the first version of this
    fetched every app in the pod and then showed twelve.
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
            .limit(limit)
        )
    ).all()
    return [
        PodAppSummary(
            id=app_id,
            name=name,
            description=description,
            url=public_app_url(public_slug),
            status=str(app_status),
            public_slug=public_slug,
        )
        for app_id, name, description, public_slug, app_status in rows
    ]


def readable_apps_ranked_by_pod(contexts: Mapping[UUID, Context]) -> Subquery:
    """Apps each pod's own viewer may read, numbered within their pod, newest
    first -- the statement ``list_readable_apps_by_pod`` caps.

    A context per pod, as ``agent.contracts.pod_summaries`` takes one and for
    the same reason: an app carries its own visibility and owner, and the
    permissions that decide it are pod-scoped -- one context built for pod A
    holds none of the caller's permissions in pod B. The OR of per-pod
    expressions is what keeps it one statement.
    """
    readable = or_(
        *[
            and_(
                AppModel.pod_id == pod_id,
                allowed_actions_contains(
                    allowed_actions_expr(
                        ctx=ctx,
                        resource_type=ResourceType.APP,
                        resource_id_col=AppModel.id,
                        pod_id_col=AppModel.pod_id,
                        owner_user_id_col=AppModel.user_id,
                        visibility_col=AppModel.visibility,
                    ),
                    Permissions.APP_READ,
                ),
            )
            for pod_id, ctx in contexts.items()
        ]
    )
    return (
        select(
            AppModel.id,
            AppModel.pod_id,
            AppModel.name,
            AppModel.description,
            AppModel.public_slug,
            AppModel.status,
            AppModel.visibility,
            AppModel.updated_at,
            func.row_number()
            .over(
                partition_by=AppModel.pod_id,
                order_by=(AppModel.updated_at.desc(), AppModel.id.desc()),
            )
            .label("rank"),
        )
        .where(AppModel.pod_id.in_(list(contexts)), readable)
        .subquery("ranked")
    )


async def list_readable_apps_by_pod(
    *,
    session: AsyncSession,
    contexts: Mapping[UUID, Context],
    limit: int,
) -> dict[UUID, list[ReadablePodApp]]:
    """Each pod's apps that pod's own viewer may read, newest first, in one query.

    Unlike ``list_app_summaries_by_pod`` this one does its own authorization
    (``readable_apps_ranked_by_pod``), which is what lets a list across every
    pod show no app its reader could not open.

    ``limit`` caps each pod rather than the whole answer, so one pod's apps
    cannot crowd another's out; it is a ceiling no ordinary pod reaches, not a
    page. Pods with no readable apps are absent.
    """
    if not contexts:
        return {}
    ranked = readable_apps_ranked_by_pod(contexts)
    rows = (
        await session.execute(
            select(
                ranked.c.id,
                ranked.c.pod_id,
                ranked.c.name,
                ranked.c.description,
                ranked.c.public_slug,
                ranked.c.status,
                ranked.c.visibility,
                ranked.c.updated_at,
            )
            .where(ranked.c.rank <= limit)
            .order_by(ranked.c.pod_id, ranked.c.updated_at.desc(), ranked.c.id.desc())
            .limit(limit * len(contexts))
        )
    ).all()
    apps: dict[UUID, list[ReadablePodApp]] = defaultdict(list)
    for (
        app_id,
        pod_id,
        name,
        description,
        public_slug,
        app_status,
        visibility,
        updated_at,
    ) in rows:
        apps[pod_id].append(
            ReadablePodApp(
                id=app_id,
                pod_id=pod_id,
                name=name,
                description=description,
                url=public_app_url(public_slug),
                status=str(app_status),
                visibility=str(visibility),
                updated_at=updated_at,
            )
        )
    return dict(apps)
