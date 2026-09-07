from datetime import datetime, timezone
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, Field


class Entity(BaseModel):
    """Base Domain Entity."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID = Field(default_factory=uuid7)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuthenticatedPrincipal(Entity):
    """Who the auth middleware decided the request is, and nothing more.

    `app/core/security.py` is the only thing that assigns `request.state.user`,
    and it constructs one of these from a verified token: an id, and the
    timestamps `Entity` gives every model. It deliberately carries no email, no
    superuser flag and no profile -- reading a user record on every request is
    what the session token exists to avoid.

    It lived in `mod:identity` as `AuthUserEntity`, which made `app/core` import
    a module to name the thing core itself creates. Identity keeps the alias so
    the old spelling still resolves.
    """


class CreatedEntity(BaseModel):
    """Base entity for append-only records."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID = Field(default_factory=uuid7)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
