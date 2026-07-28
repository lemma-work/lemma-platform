from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.modules.apps.domain.entities import AppStatus
from app.modules.apps.infrastructure.repositories import AppRepository


@dataclass(frozen=True)
class ReadyPodApp:
    id: UUID
    pod_id: UUID
    name: str
    public_slug: str


async def get_ready_pod_app(
    *,
    uow,
    pod_id: UUID,
    app_id: UUID | None,
    ctx: Any | None = None,
) -> ReadyPodApp | None:
    if app_id is None:
        return None
    app = await AppRepository(uow).get(app_id, ctx=ctx)
    if (
        app is None
        or app.id is None
        or app.pod_id != pod_id
        or app.status is not AppStatus.READY
    ):
        return None
    return ReadyPodApp(
        id=app.id,
        pod_id=app.pod_id,
        name=app.name,
        public_slug=app.public_slug,
    )
