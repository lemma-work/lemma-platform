"""Operation entities.

Operations come from two places with genuinely different lifecycles and
visibility, so they are two entities over two tables:

* :class:`ConnectorOperationEntity` -- the global catalog, shipped with the
  release and identical for every organization.
* :class:`InstallOperationEntity` -- discovered per install (MCP tools,
  OpenAPI-URL endpoints) and owned by one organization.

Everything downstream of resolution consumes :class:`ResolvedOperation`, which
is whichever of the two won the lookup, so callers never branch on the source.
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.connectors.domain.connector import (
    AuthProvider,
    ConnectorKind,
    kind_to_provider,
    provider_to_kind,
)


class _OperationFields(BaseModel):
    """Fields shared by both operation entities."""

    name: str = Field(..., description="Public operation name, normalized to lowercase")
    provider_operation_name: Optional[str] = Field(
        default=None,
        description="Provider-specific operation name used during execution",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Optional human-friendly operation name",
    )
    description: Optional[str] = Field(
        default=None, description="Operation description"
    )
    search_document: Optional[str] = Field(
        default=None,
        description="Searchable text used for discovery and ranking.",
    )
    input_schema: Optional[dict[str, Any]] = Field(
        default=None,
        description="JSON schema describing the operation input",
    )
    output_schema: Optional[dict[str, Any]] = Field(
        default=None,
        description="JSON schema describing the operation output",
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def execution_name(self) -> str:
        return self.provider_operation_name or self.name


class ConnectorOperationEntity(_OperationFields):
    """A global catalog operation, seeded from the release."""

    id: str = Field(..., description="Unique catalog ID for the operation")
    connector_id: str = Field(..., description="Connector ID")
    kind: ConnectorKind = Field(
        default=ConnectorKind.PACKAGE,
        description="Install kind whose executor runs this operation",
    )
    execution: Optional[dict[str, Any]] = Field(
        default=None,
        description="Execution descriptor; None for package-executed operations",
    )

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


class InstallOperationEntity(_OperationFields):
    """An operation discovered for one install, owned by one organization."""

    id: UUID = Field(..., description="Row id")
    auth_config_id: UUID = Field(..., description="Install this operation belongs to")
    organization_id: UUID = Field(..., description="Owning organization")
    execution: dict[str, Any] = Field(
        ..., description="Execution descriptor; always present for discovered ops"
    )


class ResolvedOperation(BaseModel):
    """The operation a call resolved to, regardless of where it came from."""

    model_config = ConfigDict(frozen=True)

    name: str
    provider_operation_name: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    execution: Optional[dict[str, Any]] = None
    source: Literal["catalog", "install"] = "catalog"

    @property
    def execution_name(self) -> str:
        return self.provider_operation_name or self.name

    @classmethod
    def from_catalog(cls, entity: ConnectorOperationEntity) -> "ResolvedOperation":
        return cls(
            name=entity.name,
            provider_operation_name=entity.provider_operation_name,
            input_schema=entity.input_schema,
            output_schema=entity.output_schema,
            execution=entity.execution,
            source="catalog",
        )

    @classmethod
    def from_install(cls, entity: InstallOperationEntity) -> "ResolvedOperation":
        return cls(
            name=entity.name,
            provider_operation_name=entity.provider_operation_name,
            input_schema=entity.input_schema,
            output_schema=entity.output_schema,
            execution=entity.execution,
            source="install",
        )
