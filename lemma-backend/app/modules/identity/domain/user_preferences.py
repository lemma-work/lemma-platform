from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UserPreferences(BaseModel):
    """Typed container for a user's cross-cutting preferences (stored as JSONB
    on ``users.preferences``).

    ``default_surfaces`` maps a surface platform value (e.g. ``"WHATSAPP"``) to
    the surface id the user prefers when the same external identity resolves to
    several pods — e.g. a person reachable via the shared system bot/number who
    belongs to pods in more than one organization. Absent platform → no default.

    ``home_seen`` maps an organization id to when the user last marked its Home
    seen, so Home can say what moved since on any browser or device. Written
    only by ``infrastructure/organization_home_seen``, one key at a time; it is
    declared here so every other writer of preferences, which round-trips this
    model, keeps it.
    """

    default_surfaces: dict[str, UUID] = Field(default_factory=dict)
    home_seen: dict[UUID, datetime] = Field(default_factory=dict)

    def default_surface_for(self, platform: str) -> UUID | None:
        return self.default_surfaces.get(str(platform).upper())

    def with_default_surface(
        self, platform: str, surface_id: UUID
    ) -> "UserPreferences":
        """Return a copy with ``platform``'s default set to ``surface_id``."""
        merged = dict(self.default_surfaces)
        merged[str(platform).upper()] = surface_id
        return self.model_copy(update={"default_surfaces": merged})

    def without_default_surface(self, platform: str) -> "UserPreferences":
        """Return a copy with ``platform``'s default cleared (if present)."""
        merged = dict(self.default_surfaces)
        merged.pop(str(platform).upper(), None)
        return self.model_copy(update={"default_surfaces": merged})
