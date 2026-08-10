"""Connector catalog entity and the per-kind capability specs.

A connector is a catalog entry (``gmail``, ``slack``, ``sql``). *How* an
organization talks to it is the **kind**: ``composio`` (Composio brokers auth and
execution), ``package`` (a vendored ``lemma-connectors`` client), ``http`` (an
OpenAPI descriptor executed directly), ``sql``, or ``mcp``.

Kind is the single runtime discriminator. It is stored on the *auth config*, not
here, because one catalog entry can legitimately be installed either way --
``gmail``, ``google_drive``, ``slack`` and ``jira`` all ship as both a vendored
package and a Composio toolkit. The connector row only advertises which kinds it
supports; an install pins exactly one.

``AuthProvider`` survives as a deprecated compatibility shim for callers outside
this module (agent surfaces, schedule composition, pod bundles) and is removed
once they migrate.
"""

import datetime
import enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class AuthScheme(str, enum.Enum):
    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    NOAUTH = "NOAUTH"


class ConnectorKind(str, enum.Enum):
    """How an install authenticates, discovers and executes operations."""

    COMPOSIO = "composio"
    HTTP = "http"
    SQL = "sql"
    MCP = "mcp"
    PACKAGE = "package"


class DiscoveryMode(str, enum.Enum):
    """Where an install's operation set comes from.

    ``NONE`` means the catalog holds every operation (Composio toolkits, vendored
    packages, connectors with a spec bundled at import time such as GitHub).
    ``MCP``/``OPENAPI`` mean the operations are discovered per install and stored
    against the auth config.
    """

    NONE = "none"
    MCP = "mcp"
    OPENAPI = "openapi"


# Deprecated in favour of ConnectorKind, and retained so callers outside this
# module keep working for one release. Read paths map through kind_to_provider;
# LEMMA means "any non-Composio kind" and so cannot round-trip back to a single
# kind on its own.
#
# Deliberately a comment rather than a docstring: this enum is reachable from
# response models, and pydantic publishes a class docstring as the schema's
# `description` -- an internal deprecation note would become part of the public
# OpenAPI contract and force an API version bump.
class AuthProvider(str, enum.Enum):
    LEMMA = "LEMMA"
    COMPOSIO = "COMPOSIO"


def kind_to_provider(kind: "ConnectorKind | str") -> AuthProvider:
    """Map a kind onto the legacy provider vocabulary."""
    value = kind.value if isinstance(kind, ConnectorKind) else str(kind)
    return (
        AuthProvider.COMPOSIO
        if value == ConnectorKind.COMPOSIO.value
        else AuthProvider.LEMMA
    )


def provider_to_kind(provider: "AuthProvider | str") -> ConnectorKind:
    """Best-effort inverse of :func:`kind_to_provider`.

    ``LEMMA`` is ambiguous -- it covers package/http/sql/mcp -- so it resolves to
    ``PACKAGE``, which is what every pre-existing native install actually was.
    Callers that know the real kind must pass it explicitly rather than rely on
    this.
    """
    value = provider.value if isinstance(provider, AuthProvider) else str(provider)
    return (
        ConnectorKind.COMPOSIO
        if value == AuthProvider.COMPOSIO.value
        else ConnectorKind.PACKAGE
    )


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


class KindSpecBase(BaseModel):
    """Catalog metadata for one way of installing a connector.

    Never instantiated directly -- each concrete subclass narrows ``kind`` to a
    ``Literal`` so the union discriminates on it.
    """

    kind: ConnectorKind
    auth_scheme: AuthScheme = AuthScheme.OAUTH2
    oauth2_defaults: OAuth2Defaults | None = None
    system_oauth: SystemOAuthCredentialRef | None = None
    supports_org_custom_oauth: bool = False
    # Runtime-enriched from the environment; never meaningful as persisted.
    system_default_available: bool = False
    # JSON Schema for the install's own config (``auth_configs.config``).
    # Validated on every create/update -- unlike the legacy path, which skipped
    # validation entirely for non-OAuth2 native connectors.
    #
    # Named for the wire, not for the concept: this is what the catalog JSON,
    # the stored capability blob and the public API all call it, and renaming it
    # would break all three at once. `install_schema` below is the name the
    # newer code reads it by.
    auth_config_schema: dict[str, Any] | None = None
    # JSON Schema for a connected account's credentials.
    credential_schema: dict[str, Any] | None = None
    discovery: DiscoveryMode = DiscoveryMode.NONE
    # Catalog-curated operation names (tried in order) that fetch the connected
    # account's own profile, so display_name resolution doesn't depend on the
    # OAuth response alone.
    profile_operation_names: list[str] | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def install_schema(self) -> dict[str, Any] | None:
        """The install-config schema, under its conceptual name."""
        return self.auth_config_schema

    @property
    def provider(self) -> AuthProvider:
        """Deprecated. The legacy provider this kind maps onto."""
        return kind_to_provider(self.kind)


class PackageKindSpec(KindSpecBase):
    kind: Literal[ConnectorKind.PACKAGE] = ConnectorKind.PACKAGE
    package_name: str | None = None


class ComposioKindSpec(KindSpecBase):
    """A toolkit Composio brokers on Lemma's behalf.

    Always system-credentialed: every Composio toolkit runs on Lemma's own
    Composio account, so ``system_default_available`` is always True and an
    install is always SYSTEM_DEFAULT (see
    ``COMPOSIO_SYSTEM_CREDENTIALS_ONLY``). A ``supports_org_custom_auth_config``
    flag used to sit here promising the opposite; it was hardcoded False by the
    only thing that produced it, force-set False again on read, and rejected
    two layers earlier than the one branch that consulted it -- so it never
    carried information. ``supports_org_custom_oauth`` on the base class is the
    real "org may bring its own client" flag, and it belongs to the kinds that
    can honour it.

    ``auth_config_schema`` is NOT an org install form here: for a non-OAuth
    toolkit the catalog fills it with the *end user's* credential fields
    (derived from Composio's ``connected_account_initiation``), which is what
    the connect dialog renders.
    """

    kind: Literal[ConnectorKind.COMPOSIO] = ConnectorKind.COMPOSIO
    auth_scheme: AuthScheme = AuthScheme.OAUTH2
    toolkit_slug: str
    system_default_available: bool = True


class HttpKindSpec(KindSpecBase):
    kind: Literal[ConnectorKind.HTTP] = ConnectorKind.HTTP


class SqlKindSpec(KindSpecBase):
    kind: Literal[ConnectorKind.SQL] = ConnectorKind.SQL
    auth_scheme: AuthScheme = AuthScheme.API_KEY


class McpKindSpec(KindSpecBase):
    kind: Literal[ConnectorKind.MCP] = ConnectorKind.MCP
    auth_scheme: AuthScheme = AuthScheme.API_KEY


KindSpec = Annotated[
    PackageKindSpec | ComposioKindSpec | HttpKindSpec | SqlKindSpec | McpKindSpec,
    Field(discriminator="kind"),
]
KindSpecAdapter: TypeAdapter[Any] = TypeAdapter(KindSpec)


class ConnectorEntity(BaseModel):
    """Global app catalog entry plus the kinds it can be installed as."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique slug/name of the connector")
    title: str | None = Field(None, description="Display title of the connector")
    description: str | None = Field(None, description="Description of the connector")
    icon: str | None = Field(None, description="Icon URL or path")
    agent_instruction: str | None = Field(None, description="Instruction for AI agent")
    kinds: list[KindSpec] = Field(
        default_factory=list, description="Ways this connector can be installed"
    )
    # Transitional runtime-only fields. The auth providers still read the
    # effective OAuth config off a model_copy'd entity rather than being handed
    # an install; they move to ResolvedInstall when the kind plugins take over
    # authentication, and these go with them. Never persisted.
    oauth2_config: OAuth2Config | None = Field(
        None,
        description="Runtime-only effective OAuth2 config; never persisted on connectors.",
    )
    composio_toolkit_slug: str | None = Field(
        None,
        description="Runtime-only Composio toolkit slug selected by auth config.",
    )
    is_active: bool = Field(default=True, description="Whether the connector is active")
    created_at: datetime.datetime | None = Field(None, description="Created at")
    updated_at: datetime.datetime | None = Field(None, description="Updated at")

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_capabilities(cls, data: Any) -> Any:
        """Accept ``provider_capabilities=`` from callers not yet migrated.

        The capability classes are already aliases of the kind specs, so the
        values need no translation -- only the field name moved. A legacy dict
        carrying ``provider`` but no ``kind`` is mapped the same way an install
        is: ``COMPOSIO`` to the composio spec, anything else to ``package``.
        """
        if not isinstance(data, dict):
            return data
        legacy = data.get("provider_capabilities")
        if data.get("kinds") is not None or legacy is None:
            return data
        kinds: list[Any] = []
        for capability in legacy:
            if isinstance(capability, dict) and "kind" not in capability:
                capability = {
                    **capability,
                    "kind": provider_to_kind(capability.get("provider", "LEMMA")),
                }
            kinds.append(capability)
        return {**data, "kinds": kinds}

    def spec_for(self, kind: ConnectorKind | str) -> KindSpec:
        """Return the spec for ``kind``, or raise ``ValueError``."""
        wanted = kind.value if isinstance(kind, ConnectorKind) else str(kind)
        for spec in self.kinds:
            if spec.kind.value == wanted:
                return spec
        raise ValueError(f"Connector '{self.id}' cannot be installed as '{wanted}'")

    def supported_kinds(self) -> list[ConnectorKind]:
        return [spec.kind for spec in self.kinds]

    def default_kind_for_provider(self, provider: AuthProvider | str) -> ConnectorKind:
        """Resolve the legacy provider vocabulary against this connector's kinds.

        ``COMPOSIO`` maps to the composio spec; ``LEMMA`` means "whichever
        non-composio kind this connector actually ships", which is unambiguous
        because a connector never advertises two native kinds.
        """
        value = provider.value if isinstance(provider, AuthProvider) else str(provider)
        if value == AuthProvider.COMPOSIO.value:
            return ConnectorKind.COMPOSIO
        for spec in self.kinds:
            if spec.kind is not ConnectorKind.COMPOSIO:
                return spec.kind
        raise ValueError(f"Connector '{self.id}' has no native kind")

    # --- deprecated provider-shaped accessors ------------------------------

    @property
    def provider_capabilities(self) -> list[KindSpec]:
        """Deprecated alias for :attr:`kinds`."""
        return self.kinds

    def capability_for(self, provider: AuthProvider | str) -> KindSpec:
        """Deprecated. Use :meth:`spec_for` with a concrete kind."""
        return self.spec_for(self.default_kind_for_provider(provider))


# Internal naming aliases retained for call sites that still speak the older
# auth-scheme / provider vocabulary.
AuthMethod = AuthScheme
OperationExecutor = AuthProvider
ProviderCapability = KindSpec
ProviderCapabilityAdapter = KindSpecAdapter
LemmaProviderCapability = PackageKindSpec
ComposioProviderCapability = ComposioKindSpec
