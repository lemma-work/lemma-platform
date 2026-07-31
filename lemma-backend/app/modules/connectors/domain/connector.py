import datetime
import enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class AuthScheme(str, enum.Enum):
    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    NOAUTH = "NOAUTH"


class AuthProvider(str, enum.Enum):
    LEMMA = "LEMMA"
    COMPOSIO = "COMPOSIO"


class ConnectorKind(str, enum.Enum):
    """Execution model for a native (LEMMA-provider) connector.

    Drives runtime dispatch: operations carry an ``execution`` descriptor whose
    ``kind`` selects the executor. ``None``/absent on the capability means the
    vendored ``lemma-connectors`` package path (Gmail/Slack/Jira) or a plain
    OAuth HTTP app.
    """

    HTTP = "http"
    SQL = "sql"
    MCP = "mcp"


class OAuth2Defaults(BaseModel):
    authorization_url: str
    token_url: str | None = None
    userinfo_url: str | None = None
    revoke_url: str | None = None
    default_scopes: list[str] = Field(default_factory=list)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    access_token_path: str = "access_token"
    refresh_token_path: str = "refresh_token"


class OAuth2Config(OAuth2Defaults):
    client_id: str
    client_secret: str


class OAuth2CredentialConfig(BaseModel):
    client_id: str
    client_secret: str


class SystemOAuthCredentialRef(BaseModel):
    client_id_env: str | list[str]
    client_secret_env: str | list[str]

    def client_id_env_names(self) -> list[str]:
        return self.client_id_env if isinstance(self.client_id_env, list) else [self.client_id_env]

    def client_secret_env_names(self) -> list[str]:
        return (
            self.client_secret_env
            if isinstance(self.client_secret_env, list)
            else [self.client_secret_env]
        )


class LemmaProviderCapability(BaseModel):
    provider: Literal[AuthProvider.LEMMA] = AuthProvider.LEMMA
    auth_scheme: AuthScheme = AuthScheme.OAUTH2
    oauth2_defaults: OAuth2Defaults | None = None
    auth_config_schema: dict[str, Any] | None = None
    credential_schema: dict[str, Any] | None = None
    system_oauth: SystemOAuthCredentialRef | None = None
    supports_org_custom_oauth: bool = False
    system_default_available: bool = False
    package_name: str | None = None
    # Catalog-curated operation names (tried in order) that fetch the
    # connected account's own profile (email/name/workspace), so display_name
    # resolution doesn't depend on the OAuth response alone.
    profile_operation_names: list[str] | None = None
    # Execution model for package-free connectors (http/sql/mcp). None => vendored
    # package path or plain OAuth HTTP. Drives which executor + discoverer are used.
    kind: ConnectorKind | None = None
    # Whether an org can create many auth-config instances of this connector
    # (e.g. several SQL databases / MCP servers / OpenAPI specs).
    supports_multiple_instances: bool = False


class ComposioProviderCapability(BaseModel):
    provider: Literal[AuthProvider.COMPOSIO] = AuthProvider.COMPOSIO
    auth_scheme: AuthScheme = AuthScheme.OAUTH2
    toolkit_slug: str
    auth_config_schema: dict[str, Any] | None = None
    system_default_available: bool = True
    supports_org_custom_auth_config: bool = False
    profile_operation_names: list[str] | None = None


ProviderCapability = Annotated[
    LemmaProviderCapability | ComposioProviderCapability,
    Field(discriminator="provider"),
]
ProviderCapabilityAdapter = TypeAdapter(ProviderCapability)


class ConnectorEntity(BaseModel):
    """Global app catalog entry plus typed provider capability metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique slug/name of the connector")
    title: str | None = Field(None, description="Display title of the connector")
    description: str | None = Field(None, description="Description of the connector")
    icon: str | None = Field(None, description="Icon URL or path")
    agent_instruction: str | None = Field(None, description="Instruction for AI agent")
    provider_capabilities: list[ProviderCapability] = Field(default_factory=list)
    oauth2_config: OAuth2Config | None = Field(
        None,
        description="Runtime-only effective OAuth2 config; never persisted on connectors.",
    )
    composio_toolkit_slug: str | None = Field(
        None,
        description="Runtime-only Composio toolkit slug selected by auth config.",
    )
    composio_auth_config_id: str | None = Field(
        None,
        description="Runtime-only Composio auth config id selected by auth config.",
    )
    is_active: bool = Field(default=True, description="Whether the connector is active")
    created_at: datetime.datetime | None = Field(None, description="Created at")
    updated_at: datetime.datetime | None = Field(None, description="Updated at")

    def capability_for(self, provider: AuthProvider | str) -> ProviderCapability:
        provider_value = provider.value if hasattr(provider, "value") else str(provider)
        for capability in self.provider_capabilities:
            if capability.provider.value == provider_value:
                return capability
        raise ValueError(f"Connector '{self.id}' does not support provider '{provider_value}'")


# Internal naming aliases for code paths that still describe the auth scheme/executor
# vocabulary while persisting only provider capabilities on connectors.
AuthMethod = AuthScheme
OperationExecutor = AuthProvider
