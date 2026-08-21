"""Pod role domain entities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.modules.pod.domain.roles import PodRole

SYSTEM_ROLE_NAMES = {role.value for role in PodRole}


class PodRoleEntity(BaseModel):
    id: UUID
    pod_id: UUID
    name: str
    is_system: bool = False
    created_by_user_id: UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
