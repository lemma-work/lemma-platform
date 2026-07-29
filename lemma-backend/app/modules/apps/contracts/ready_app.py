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


async def get_ready_pod_app_by_name(
    *,
    uow,
    pod_id: UUID,
    app_name: str | None,
    ctx: Any | None = None,
) -> ReadyPodApp | None:
    resolved_name = str(app_name or "").strip()
    if not resolved_name:
        return None
    app = await AppRepository(uow).get_by_name(pod_id, resolved_name, ctx=ctx)
    if (
        app is None
        or app.id is None
        or app.status is not AppStatus.READY
    ):
        return None
    return ReadyPodApp(
        id=app.id,
        pod_id=app.pod_id,
        name=app.name,
        public_slug=app.public_slug,
    )
