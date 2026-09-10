from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, JsonValue


class PendingState(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    binding_key: str
    platform: str
    step: str
    challenge_id: UUID | None
    user_id: UUID | None
    installation_surface_id: UUID | None
    verified_phone: str | None
    destination: dict[str, JsonValue]
    original_event: dict[str, JsonValue] | None
    expires_at: datetime
    ready_at: datetime | None
    handed_off_at: datetime | None
