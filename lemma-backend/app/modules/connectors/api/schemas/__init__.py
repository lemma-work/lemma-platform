from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationSummary,
)
from app.modules.connectors.domain.connector import AuthScheme, ConnectorKind


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


class ConnectorKindResponseSchema(BaseModel):
    """One way a connector can be installed.

    Flat rather than a union over the five kinds: a client's job here is to
    decide what to put in an install's `config`, and for that `kind` plus the
    schemas is the whole answer. The per-kind extras below are populated only
    where they apply.
    """

    model_config = ConfigDict(from_attributes=True)

    kind: ConnectorKind
    auth_scheme: AuthScheme = AuthScheme.OAUTH2
    oauth2_defaults: Optional[OAuth2DefaultsResponseSchema] = None
    # Reads the domain model's `auth_config_schema` as well as its own name:
    # `connector.get` builds its response by dumping this model and validating
    # the result again, so a single validation alias would drop the value on
    # the second pass.
    config_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        validation_alias=AliasChoices("auth_config_schema", "config_schema"),
        description="JSON Schema for an install's `config`.",
    )
    credential_schema: Optional[Dict[str, Any]] = None
    supports_org_custom_oauth: bool = False
    system_default_available: bool = False
    discovery: str = "none"
    # `package` only.
    package_name: Optional[str] = None
    # `composio` only.
    toolkit_slug: Optional[str] = None


# Connector Schemas
class ConnectorResponseSchema(BaseSchema):
    """Schema for connector response."""

    id: str
    # name: str  # Name removed or optional if id is the identifier
    title: Optional[str] = None
    description: Optional[str]
    kinds: List[ConnectorKindResponseSchema] = Field(default_factory=list)
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
    """Schema for connect request response.

    `attributes` is deliberately absent. The row carries the live OAuth
    `state`, the provider's own handle on the authorization, and the PKCE
    verifier -- the three things that have to survive the redirect and the
    three the caller has no use for. Returning them put the verifier and the
    `state` into browser memory, client-side logs and any HAR capture, which is
    the exposure PKCE exists to survive. The client needs `authorization_url`
    and `id`.
    """

    id: UUID
    user_id: UUID
    organization_id: UUID
    auth_config_id: UUID
    connector_id: str
    authorization_url: Optional[str]
    status: str
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
    display_name: Optional[str] = None
    preferences: Optional[Dict[str, Any]]
    allowed_scopes: Optional[List[str]]
    # Include connector info
    connector: Optional[ConnectorResponseSchema] = None
    # The kind of the install backing this account. Not on
    # AccountEntity itself (it lives on the auth config), so the controller
    # sets it explicitly after model_validate via ConnectorService.get_account_kind.
    kind: Optional[str] = None
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


class AccountCredentialsUpdateSchema(BaseModel):
    """A replacement credential for an account that already exists."""

    credentials: Dict[str, Any] = Field(
        ..., description="The new credential, validated against the kind's schema"
    )


class AuthConfigCreateSchema(BaseModel):
    connector_id: str
    kind: Optional[str] = Field(
        default=None,
        description=(
            "Which of the connector's kinds to install. Optional when the "
            "connector offers only one."
        ),
    )
    config_source: str = "SYSTEM_DEFAULT"
    config: Optional[Dict[str, Any]] = None
    name: Optional[str] = None


class AuthConfigResponseSchema(BaseSchema):
    id: UUID
    organization_id: UUID
    connector_id: str
    kind: str
    config_source: str
    status: str
    name: str
    is_default: bool = False
    auth_scheme: Optional[str] = Field(
        default=None,
        description=(
            "How this install authenticates, which is not always what the "
            "connector's catalog entry says. `mcp` is one catalog entry "
            "standing for every server a tenant may point at: the entry says "
            "API_KEY, but an install whose server described its own "
            "authorization when it was created signs in through a browser and "
            "answers OAUTH2 here. Branch on this rather than on the "
            "connector's kind when deciding how to connect an install."
        ),
    )
    config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AuthConfigUpdateSchema(BaseModel):
    name: Optional[str] = Field(
        default=None,
        description=(
            "New name for this install. Accounts follow the rename, since they "
            "reference the install by id."
        ),
    )
    config: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Replacement configuration. Re-validated against the connector's "
            "schema, and re-checked against the network-target guard."
        ),
    )
    status: Optional[str] = Field(default=None, description="ACTIVE or DISABLED.")
    is_default: Optional[bool] = Field(
        default=None,
        description=(
            "Make this the install that a bare connector_id resolves to. "
            "Demotes whichever install currently holds that role."
        ),
    )


class OperationDiscoveryStatus(str, Enum):
    """Whether a discovery attempt worked, was never made, or was refused."""

    OK = "ok"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class OperationDiscoverySchema(BaseModel):
    """What re-reading an install's operation list actually did.

    `operation_count` alone cannot say: a connector with no operations to
    advertise, a kind whose operations are static, and a server that refused
    the listing all report zero. They need different things from the reader --
    nothing, nothing, and a retry once the server is reachable -- so the status
    is the field to branch on and the count is detail.
    """

    status: OperationDiscoveryStatus
    operation_count: int = Field(
        default=0,
        description="Operations stored for the install. Zero unless status is ok.",
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Machine-readable cause when status is not ok: the connector error "
            "code for a refused discovery, or why none was attempted."
        ),
    )


class AuthConfigUpdateResponseSchema(BaseModel):
    auth_config: AuthConfigResponseSchema
    operations_discovered: int = Field(
        default=0,
        description=(
            "Operations re-discovered because the change altered where they "
            "come from. Zero for a connector whose operations are static, and "
            "also zero when discovery was refused -- read "
            "`operations_discovery.status` to tell those apart."
        ),
    )
    operations_discovery: OperationDiscoverySchema = Field(
        description="Whether the re-discovery this change triggered succeeded."
    )
    accounts_marked_for_reauth: int = Field(
        default=0,
        description=(
            "Connected accounts flagged for reconnect because the change "
            "invalidated their stored credentials. They are never deleted: the "
            "account keeps its id and grants, and reconnecting updates it in "
            "place, so anything referencing it keeps working."
        ),
    )


class AuthConfigOperationsRefreshResponseSchema(OperationDiscoverySchema):
    """The result of the refresh endpoint, which is the recovery path.

    It reports the outcome rather than only a count because it exists for the
    case where discovery already failed once: answering `{"operation_count": 0}`
    to a server that refused the listing again told the operator their retry
    had worked.
    """

    auth_config_name: str


class AuthConfigListResponseSchema(BaseModel):
    items: List[AuthConfigResponseSchema]
    limit: int
    next_page_token: Optional[str] = None


# Trigger Schemas
class AppTriggerResponseSchema(BaseSchema):
    """Schema for trigger response."""

    id: str  # String ID (slug)
    connector_id: Optional[str]
    kind: ConnectorKind
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
    kind: ConnectorKind
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
    kind: str


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
    kind: Optional[str] = None
