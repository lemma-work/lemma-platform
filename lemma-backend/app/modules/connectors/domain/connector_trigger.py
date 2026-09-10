from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.modules.connectors.domain.connector import (
    AuthProvider,
    ConnectorKind,
    kind_to_provider,
)


class ConnectorTriggerEntity(BaseModel):
    """Available trigger entity for connector events."""

    id: str = Field(..., description="Unique slug/name of the trigger")
    connector_id: str = Field(..., description="ID of the connector")
    kind: ConnectorKind = Field(
        default=ConnectorKind.HTTP,
        description="Install kind that emits this trigger",
    )
    # name field is redundant if id is name-based, but could be useful for display
    event_type: str = Field(..., description="Type of the event for platform")
    description: Optional[str] = Field(None, description="Description of the trigger")
    config_schema: Optional[Dict[str, Any]] = Field(
        None, description="Schema for configuration"
    )
    payload_schema: Optional[Dict[str, Any]] = Field(
        None, description="Schema for payload"
    )
    payload_example: Optional[Dict[str, Any]] = Field(
        None, description="Example payload"
    )
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def provider(self) -> AuthProvider:
        """Deprecated alias derived from :attr:`kind`."""
        return kind_to_provider(self.kind)
