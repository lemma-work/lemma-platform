from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.domain.entity import Entity
from app.modules.connectors.domain.connector import AuthProvider


class AuthConfigStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AuthConfigSource(str, enum.Enum):
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"
    ORG_CUSTOM = "ORG_CUSTOM"


class AuthConfigEntity(Entity):
    """Organization-level connector provider configuration."""

    organization_id: UUID
    connector_id: str
    provider: AuthProvider = AuthProvider.LEMMA
    config_source: AuthConfigSource = AuthConfigSource.SYSTEM_DEFAULT
    status: AuthConfigStatus = AuthConfigStatus.ACTIVE
    name: str
    provider_config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    created_by_user_id: UUID | None = None
    updated_by_user_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def uses_composio(self) -> bool:
        return self.provider == AuthProvider.COMPOSIO

    @property
    def uses_native(self) -> bool:
        return self.provider == AuthProvider.LEMMA
