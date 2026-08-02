from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.connectors.domain.connector import (
    AuthProvider,
    ConnectorKind,
    kind_to_provider,
    provider_to_kind,
)


class ConnectorTriggerEntity(BaseModel):
    """Available trigger entity for connector events."""

    id: str = Field(..., description="Unique slug/name of the trigger")
    connector_id: str = Field(..., description="ID of the connector")
    kind: ConnectorKind = Field(
        default=ConnectorKind.PACKAGE,
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

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_provider(cls, data: Any) -> Any:
        """Accept ``provider=`` from callers not yet migrated to kinds."""
        if not isinstance(data, dict):
            return data
        if data.get("kind") is None and data.get("provider") is not None:
            data = {**data, "kind": provider_to_kind(data["provider"])}
        return data

    @property
    def provider(self) -> AuthProvider:
        """Deprecated alias derived from :attr:`kind`."""
        return kind_to_provider(self.kind)

    @property
    def config_field_names(self) -> List[str]:
        """Get the field names from the config schema."""
        # return field names from json schema of config_schema
        if not self.config_schema:
            return []
        field_names = []
        for field_name, field_schema in self.config_schema.get(
            "properties", {}
        ).items():
            field_names.append(field_name)
        return field_names
