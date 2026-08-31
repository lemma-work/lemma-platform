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
