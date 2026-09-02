from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain.entity import Entity
from app.modules.connectors.domain.connector import ConnectorEntity


class OAuthCredentials(BaseModel):
    """OAuth credentials for Lemma-managed auth."""

    model_config = ConfigDict(extra="allow")

    access_token: Optional[str] = Field(
        None,
        description=(
            "OAuth access token. Optional because Composio-managed connections "
            "are addressed by connection_id and may not surface a raw token."
        ),
    )
    refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
    token_type: str = Field(default="Bearer", description="Type of the token")
    expires_at: Optional[datetime] = Field(
        None, description="Expiration time of the access token"
    )
    raw_response: Optional[dict] = Field(
        None, description="Raw response from the OAuth provider"
    )
    # Additional fields that might be stored
    connection_id: Optional[str] = Field(
        None, description="Connection ID if applicable"
    )
    user_data: Optional[dict] = Field(None, description="User data from provider")


class LemmaAPIKeyCredentials(BaseModel):
    """API key credentials for Lemma-managed auth."""

    model_config = ConfigDict(extra="allow")

    api_key: str = Field(..., description="API key for the account")
    api_secret: Optional[str] = Field(None, description="API secret for the account")


class ComposioCredentials(BaseModel):
    """Credentials for Composio auth provider"""

    model_config = ConfigDict(extra="allow")

    connection_id: str = Field(..., description="The connection id for the account")
    # Optional cached token to avoid frequent API calls
    access_token: Optional[str] = Field(None, description="Cached access token")
    token_expires_at: Optional[datetime] = Field(
        None, description="Expiration time of the cached token"
    )


class GenericCredentials(BaseModel):
    """Credential payload for app-specific native connectors."""

    model_config = ConfigDict(extra="allow")


# Union type for all credential types
CredentialTypes = Union[
    OAuthCredentials,
    LemmaAPIKeyCredentials,
    ComposioCredentials,
    GenericCredentials,
]


class AccountStatus(str, enum.Enum):
    CONNECTED = "CONNECTED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    DISCONNECTED = "DISCONNECTED"


class AccountEntity(Entity):
    """User account entity for third-party connectors."""

    user_id: UUID = Field(..., description="ID of the user owning the account")
    organization_id: UUID = Field(..., description="ID of the organization scope")
    auth_config_id: UUID = Field(..., description="ID of the org auth config")
    connector_id: str = Field(..., description="ID of the connected connector")
    is_default: bool = Field(
        default=False,
        description=(
            "Whether this is the default account for (user, auth_config), used "
            "when an account is resolved without an explicit account_id."
        ),
    )
    status: AccountStatus = Field(default=AccountStatus.CONNECTED)
    provider_account_id: Optional[str] = Field(
        None, description="Provider-side user/account identifier"
    )
    external_ref: Optional[str] = Field(
        None,
        description=(
            "Opaque upstream id this account's events arrive under, used to "
            "route an inbound delivery back to it."
        ),
    )

    email: Optional[str] = Field(None, description="Email associated with the account")
    display_name: Optional[str] = Field(
        None,
        description=(
            "Human-friendly label for the account (e.g. a bot @username, mailbox "
            "address, workspace, or phone) so a user can tell several accounts of "
            "the same app apart. Derived at connect time."
        ),
    )

    credentials: Optional[CredentialTypes] = Field(
        None, description="Stored credentials"
    )
    preferences: Optional[Dict[str, Any]] = Field(
        None, description="User preferences for this connector"
    )
    allowed_scopes: Optional[List[str]] = Field(
        None, description="List of allowed scopes"
    )

    connector: Optional[ConnectorEntity] = Field(
        None, description="Associated connector"
    )
