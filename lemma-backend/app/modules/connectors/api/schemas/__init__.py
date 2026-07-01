from __future__ import annotations

import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Union, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict

from app.modules.connectors.domain.connector import AuthProvider, AuthScheme
from app.modules.connectors.api.schemas.connector_operation_schemas import OperationSummary


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(from_attributes=True)


class OAuth2DefaultsResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    authorization_url: str
    token_url: Optional[str] = None
    userinfo_url: Optional[str] = None
    revoke_url: Optional[str] = None
    default_scopes: List[str] = Field(default_factory=list)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    access_token_path: str = "access_token"
    refresh_token_path: str = "refresh_token"


class LemmaProviderCapabilityResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: Literal[AuthProvider.LEMMA] = AuthProvider.LEMMA
    auth_scheme: AuthScheme = AuthScheme.OAUTH2
    oauth2_defaults: Optional[OAuth2DefaultsResponseSchema] = None
    auth_config_schema: Optional[Dict[str, Any]] = None
    credential_schema: Optional[Dict[str, Any]] = None
    supports_org_custom_oauth: bool = False
    system_default_available: bool = False
    package_name: Optional[str] = None


class ComposioProviderCapabilityResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: Literal[AuthProvider.COMPOSIO] = AuthProvider.COMPOSIO
    auth_scheme: AuthScheme = AuthScheme.OAUTH2
    toolkit_slug: str
    auth_config_schema: Optional[Dict[str, Any]] = None
    system_default_available: bool = True
    supports_org_custom_auth_config: bool = False


ProviderCapabilityResponseSchema = Union[
    LemmaProviderCapabilityResponseSchema,
    ComposioProviderCapabilityResponseSchema,
]


# Connector Schemas
class ConnectorResponseSchema(BaseSchema):
    """Schema for connector response."""

    id: str
    # name: str  # Name removed or optional if id is the identifier
    title: Optional[str] = None
    description: Optional[str]
    provider_capabilities: List[ProviderCapabilityResponseSchema] = Field(
        default_factory=list
    )
    icon: Optional[str]
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConnectorDetailResponseSchema(ConnectorResponseSchema):
    """Schema for connector details including operation catalog."""

    operations: Dict[str, OperationSummary] = Field(default_factory=dict)


class ConnectorListResponseSchema(BaseModel):
    """Schema for connector list response."""

    items: List[ConnectorResponseSchema]
    limit: int
    next_page_token: Optional[str] = None


# Connect Request Schemas
class ConnectRequestInitiateSchema(BaseModel):
    """Schema for initiating a connect request."""

    connector_id: str | None = Field(None, description="Connector ID to connect")
    auth_config_id: UUID | None = Field(None, description="Auth config ID to connect")


class ConnectRequestResponseSchema(BaseSchema):
    """Schema for connect request response."""

    id: UUID
    user_id: UUID
    organization_id: UUID
    auth_config_id: UUID
    connector_id: str
    authorization_url: Optional[str]
    status: str
    attributes: Optional[Dict[str, Any]]
    created_at: datetime.datetime
    updated_at: datetime.datetime


# Account Schemas
class AccountResponseSchema(BaseSchema):
    """Schema for account response."""

    id: UUID
    user_id: UUID
    organization_id: UUID
    auth_config_id: UUID
    connector_id: str
    is_default: bool = False
    status: str
    provider_account_id: Optional[str] = None
    email: Optional[str]
    preferences: Optional[Dict[str, Any]]
    allowed_scopes: Optional[List[str]]
    # Include connector info
    connector: Optional[ConnectorResponseSchema] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AccountListResponseSchema(BaseModel):
    """Schema for account list response."""

    items: List[AccountResponseSchema]
    limit: int
    next_page_token: Optional[str] = None


class AccountCreateSchema(BaseModel):
    """Schema for directly connecting a credential-managed native account."""

    auth_config_id: UUID | None = Field(None, description="Auth config ID to connect")
    auth_config_name: str | None = Field(
        None, description="Auth config name to connect"
    )
    credentials: Dict[str, Any] = Field(default_factory=dict)
    provider_account_id: Optional[str] = None
    email: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None
    allowed_scopes: Optional[List[str]] = None


class AuthConfigCreateSchema(BaseModel):
    connector_id: str
    provider: str = "LEMMA"
    config_source: str = "SYSTEM_DEFAULT"
    credential_config: Optional[Dict[str, Any]] = None
    name: Optional[str] = None


class AuthConfigResponseSchema(BaseSchema):
    id: UUID
    organization_id: UUID
    connector_id: str
    provider: str
    config_source: str
    status: str
    name: str
    credential_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AuthConfigListResponseSchema(BaseModel):
    items: List[AuthConfigResponseSchema]
    limit: int
    next_page_token: Optional[str] = None


# Trigger Schemas
class AppTriggerResponseSchema(BaseSchema):
    """Schema for trigger response."""

    id: str  # String ID (slug)
    connector_id: Optional[str]
    provider: AuthProvider
    # name: str # Name is likely id or part of it
    description: Optional[str]
    config_schema: Optional[Dict[str, Any]]
    payload_schema: Optional[Dict[str, Any]]
    payload_example: Optional[Dict[str, Any]]
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AppTriggerSummaryResponseSchema(BaseSchema):
    """Lean trigger shape for list responses.

    Omits the heavy `config_schema` / `payload_schema` / `payload_example` JSON
    blobs — fetch those from `connector.trigger.get`.
    """

    id: str
    connector_id: Optional[str]
    provider: AuthProvider
    description: Optional[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AppTriggerListResponseSchema(BaseModel):
    """Schema for trigger list response."""

    items: List[AppTriggerSummaryResponseSchema]
    limit: int
    next_page_token: Optional[str] = None


# Message Response Schema
class MessageResponseSchema(BaseModel):
    """Schema for message response."""

    message: str
    success: bool = True


class CredentialTypes(Enum):
    """Credential types."""

    OAUTH2 = "oauth2"
    API_KEY = "api_key"


class OauthCredentialsResponseSchema(BaseModel):
    """Schema for OAuth credentials response."""

    # Optional: Composio-managed connections are addressed by connection_id and
    # may not surface a raw access token.
    access_token: Optional[str] = None
    expires_at: Optional[datetime.datetime] = None
    connection_id: Optional[str] = None


class ApiKeyCredentialsResponseSchema(BaseModel):
    """Schema for API key credentials response."""

    api_key: str
    api_secret: Optional[str] = None


class AccountCredentialsResponseSchema(BaseModel):
    """Schema for account credentials response."""

    type: CredentialTypes = CredentialTypes.OAUTH2
    data: Union[OauthCredentialsResponseSchema, ApiKeyCredentialsResponseSchema]
    user_data: Optional[dict] = None


# Connector Status schemas
class InstalledAppSummary(BaseModel):
    name: str
    connector_id: str
    title: Optional[str] = None
    status: str
    provider: str


class ConnectedAccountSummary(BaseModel):
    id: str
    connector_id: str
    title: Optional[str] = None
    email: Optional[str] = None
    status: str


class ConnectorStatusResponse(BaseModel):
    installed: List[InstalledAppSummary]
    accounts: List[ConnectedAccountSummary]


# Connector Skill schema
class ConnectorSkillResponse(BaseModel):
    connector_id: str
    title: Optional[str] = None
    markdown: str
    provider: Optional[str] = None
