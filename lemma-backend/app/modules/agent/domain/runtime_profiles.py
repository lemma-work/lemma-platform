"""Runtime profile domain models for agent execution."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

# Re-exported: the portable settings are a core type because workflow steps
# carry them too, but they are read here, beside the catalog that gates them.
from app.core.domain.generation import (
    GenerationSettings as GenerationSettings,
    ReasoningEffort as ReasoningEffort,
)
from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.value_objects import HarnessKind, JsonObject


#: The deployment's own profile, built from its environment rather than
#: stored. Bare string ids name a runtime in several places (agents,
#: conversations, runs, pod config), so this one is spelled once, here.
SYSTEM_LEMMA_PROFILE_ID = "system:lemma"

#: The metadata key an owner's "every teammate runs on this" mark is kept
#: under (`organization_default`).
ORGANIZATION_DEFAULT_KEY = "organization_default"
#: The metadata key an Agent Host profile records its coding agent under.
HARNESS_KEY = "harness_key"


class RuntimeProfileScope(str, Enum):
    SYSTEM = "SYSTEM"
    ORGANIZATION = "ORGANIZATION"
    PERSONAL = "PERSONAL"


class RuntimeProfileKind(str, Enum):
    MODEL_PROVIDER = "MODEL_PROVIDER"
    HARNESS = "HARNESS"


class RuntimeProfileProtocol(str, Enum):
    """How a profile reaches its runtime.

    The retired local daemon needed one protocol per coding tool
    (``CODEX_APP_SERVER``, ``CLAUDE_CODE``, ``OPENCODE``, ``CURSOR``,
    ``ANTIGRAVITY``). Agent Host needs one: the tool is identified by the
    profile's ``harness_id``. Stored rows can still carry a retired value, so
    the profile repository skips protocols this enum no longer knows rather
    than failing the whole listing.
    """

    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    ANTHROPIC_COMPATIBLE = "ANTHROPIC_COMPATIBLE"
    AGENT_HOST = "AGENT_HOST"


class RuntimeProfileStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class RuntimeProfileAvailability(str, Enum):
    """Whether a profile can take work right now.

    Derived from the harness and its host at read time, never persisted: the
    same profile is READY or OFFLINE depending only on whether someone's laptop
    is awake. Model-provider profiles have no availability - they are reachable
    whenever their endpoint is.
    """

    READY = "READY"
    OFFLINE = "OFFLINE"
    NOT_INSTALLED = "NOT_INSTALLED"
    UNAVAILABLE = "UNAVAILABLE"


class RuntimeModelCapability(str, Enum):
    TEXT = "TEXT"
    TOOLS = "TOOLS"
    VISION = "VISION"
    REASONING = "REASONING"


MODEL_PROVIDER_PROTOCOLS = frozenset(
    {
        RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
    }
)

HARNESS_PROTOCOLS = frozenset({RuntimeProfileProtocol.AGENT_HOST})


class RuntimeModelCatalogEntry(BaseModel):
    name: str = Field(min_length=1)
    display_name: str | None = None
    provider_model_name: str = Field(min_length=1)
    capabilities: list[RuntimeModelCapability] = Field(default_factory=list)
    # The lowest layer of a run's generation settings: what this model wants
    # unless the profile, agent or step says otherwise. Portable
    # ``GenerationSettings`` keys, plus any pydantic-ai wire key passed through
    # as written -- see ``services/generation_settings``.
    default_model_settings: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("name", "provider_model_name")
    @classmethod
    def normalize_required_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized

    @field_validator("capabilities", mode="before")
    @classmethod
    def drop_unknown_capabilities(cls, value: object) -> object:
        # A capability this build no longer knows -- one an older release or
        # a catalog customizer declared -- describes nothing the server acts
        # on. Failing the entry would fail the whole profile on read, and with
        # it every run on that provider.
        if not isinstance(value, list):
            return value
        known = {capability.value for capability in RuntimeModelCapability}
        return [
            item
            for item in value
            if isinstance(item, RuntimeModelCapability) or item in known
        ]

    @field_validator("default_model_settings")
    @classmethod
    def portable_defaults_are_valid(cls, value: JsonObject) -> JsonObject:
        # Checked where it is written, not at the run that reads it: a bad
        # value here would otherwise fail every run on this model.
        GenerationSettings.model_validate(
            {
                key: item
                for key, item in value.items()
                if key in GenerationSettings.model_fields and item is not None
            }
        )
        return value

    @property
    def reasons(self) -> bool:
        """Whether this model takes a reasoning effort or thinking budget."""
        return RuntimeModelCapability.REASONING in self.capabilities


class ApiKeyRuntimeCredentials(BaseModel):
    # SecretStr so the key never leaks via repr()/logs/tracebacks; read the
    # plaintext only through ``reveal_credentials`` at the point of use.
    api_key: SecretStr = Field(min_length=1)


RuntimeCredentials = ApiKeyRuntimeCredentials | JsonObject


def reveal_credentials(credentials: object | None) -> dict[str, object] | None:
    """Plaintext dict form of runtime credentials for actual use.

    This is the single place secrets are unwrapped: ``SecretStr`` fields become
    plain strings here, so the result must only flow to a consumer that truly
    needs the value (harness authentication, encrypted persistence) — never to a
    log, response body, or repr. Everywhere else, keep the typed credential
    object, which masks its secrets.
    """
    if credentials is None:
        return None
    model_dump = getattr(credentials, "model_dump", None)
    if callable(model_dump):
        return {
            key: (value.get_secret_value() if isinstance(value, SecretStr) else value)
            for key, value in model_dump().items()
        }
    if isinstance(credentials, dict):
        return dict(credentials)
    return None


class OpenAICompatibleRuntimeConfig(BaseModel):
    base_url: HttpUrl
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


class AnthropicCompatibleRuntimeConfig(BaseModel):
    base_url: HttpUrl | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


#: How long a dispatched Agent Host run waits for its computer to take it,
#: when the profile does not say.
DEFAULT_HOST_WAIT_TIMEOUT_SECONDS = 300


class HarnessRuntimeConfig(BaseModel):
    """Everything an Agent Host run needs beyond the harness binding itself.

    ``harness_snapshot_revision`` pins the configuration the profile was saved
    against, so a harness that changes underneath is caught at dispatch instead
    of silently running a different configuration.
    """

    harness_snapshot_revision: str = Field(min_length=1)
    config_selections: JsonObject = Field(default_factory=dict)
    host_wait_timeout_seconds: int = Field(
        default=DEFAULT_HOST_WAIT_TIMEOUT_SECONDS, ge=1
    )


RuntimeProfileConfig = (
    OpenAICompatibleRuntimeConfig
    | AnthropicCompatibleRuntimeConfig
    | HarnessRuntimeConfig
)

#: The one config shape each protocol takes. A profile's config is validated
#: against its own protocol's model, never matched against the union: the
#: Anthropic shape has no required field, so a harness config that failed its
#: own model used to be re-read as an Anthropic one -- its revision and
#: selections silently gone, and erased for good on the next save.
CONFIG_MODEL_BY_PROTOCOL: Mapping[
    RuntimeProfileProtocol, type[RuntimeProfileConfig]
] = {
    RuntimeProfileProtocol.OPENAI_COMPATIBLE: OpenAICompatibleRuntimeConfig,
    RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE: AnthropicCompatibleRuntimeConfig,
    RuntimeProfileProtocol.AGENT_HOST: HarnessRuntimeConfig,
}


def config_for_protocol(
    protocol: RuntimeProfileProtocol, config: object
) -> RuntimeProfileConfig:
    """``config`` validated as the shape ``protocol`` takes.

    Raises ``ValueError`` (a pydantic ``ValidationError``) for anything else,
    another protocol's typed config included.
    """
    model = CONFIG_MODEL_BY_PROTOCOL[protocol]
    if isinstance(config, BaseModel):
        if not isinstance(config, model):
            raise ValueError(
                f"{protocol.value} profiles take {model.__name__}, "
                f"not {type(config).__name__}"
            )
        # Re-validated rather than trusted: ``model_copy(update=...)`` builds
        # an instance without running a single validator.
        config = config.model_dump()
    try:
        return model.model_validate(config if config is not None else {})
    except ValidationError as error:
        # Restated without the input: pydantic's own text quotes it, and this
        # input can carry a provider's custom headers.
        raise ValueError(describe_invalid(error, prefix="config")) from None


def describe_invalid(error: ValidationError, *, prefix: str = "") -> str:
    """What failed and where, in words, quoting none of the input."""
    problems: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        message = str(item["msg"]).removeprefix("Value error, ")
        location = ".".join(str(part) for part in (prefix, *item["loc"]) if part)
        problems.append(f"{location}: {message}" if location else message)
    return "; ".join(problems) or "invalid value"


def invalid_profile(error: ValidationError) -> RuntimeProfileValidationError:
    """A profile that failed validation, as the 400 its caller can fix."""
    return RuntimeProfileValidationError(describe_invalid(error))


def _protocol_of(value: object) -> RuntimeProfileProtocol | None:
    if isinstance(value, RuntimeProfileProtocol):
        return value
    try:
        return RuntimeProfileProtocol(str(value))
    except ValueError:
        # Left to the field's own validation, which names the bad value.
        return None


class AgentRuntimeProfile(BaseModel):
    """Org/system profile that owns model/harness execution configuration."""

    model_config = ConfigDict(use_enum_values=False)

    id: str
    organization_id: UUID | None = None
    user_id: UUID | None = None
    harness_id: UUID | None = None
    scope: RuntimeProfileScope
    kind: RuntimeProfileKind
    protocol: RuntimeProfileProtocol
    name: str = Field(min_length=1)
    description: str | None = None
    default_model_name: str | None = None
    model_catalog: list[RuntimeModelCatalogEntry] = Field(default_factory=list)
    # Out of the repr: custom headers live here, unmasked, and a profile is
    # exactly the kind of object that ends up in a log line or a traceback.
    # Validated against ``protocol`` before the union sees it; see
    # ``CONFIG_MODEL_BY_PROTOCOL``. Absent means the protocol's empty config,
    # which only a shape with no required field accepts.
    config: RuntimeProfileConfig = Field(
        default_factory=AnthropicCompatibleRuntimeConfig, repr=False
    )
    credentials: RuntimeCredentials | None = None
    status: RuntimeProfileStatus = RuntimeProfileStatus.ACTIVE
    metadata: JsonObject = Field(default_factory=dict)
    #: The stored row's optimistic-lock counter when this was read; a write
    #: of this copy is refused once the row has moved past it. 0 for a profile
    #: that has never been stored (the system one, or one being created).
    version: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def config_matches_protocol(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        protocol = _protocol_of(data.get("protocol"))
        if protocol is None:
            return data
        return {**data, "config": config_for_protocol(protocol, data.get("config"))}

    @field_validator("id", "name")
    @classmethod
    def normalize_non_empty_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized

    @model_validator(mode="after")
    def validate_profile(self) -> "AgentRuntimeProfile":
        if not isinstance(self.config, CONFIG_MODEL_BY_PROTOCOL[self.protocol]):
            raise ValueError(f"{self.protocol.value} profile has the wrong config")
        if self.kind is RuntimeProfileKind.MODEL_PROVIDER:
            if self.protocol not in MODEL_PROVIDER_PROTOCOLS:
                raise ValueError("MODEL_PROVIDER profile has invalid protocol")
            if not self.default_model_name:
                raise ValueError("MODEL_PROVIDER profile requires default_model_name")
            self._validate_default_model_name()
        if self.kind is RuntimeProfileKind.HARNESS:
            if self.protocol not in HARNESS_PROTOCOLS:
                raise ValueError("HARNESS profile has invalid protocol")
            if self.harness_id is None:
                raise ValueError("HARNESS profile requires harness_id")
        elif self.harness_id is not None:
            raise ValueError("Only a HARNESS profile may bind a harness_id")
        if (
            self.scope
            in {RuntimeProfileScope.ORGANIZATION, RuntimeProfileScope.PERSONAL}
            and self.organization_id is None
        ):
            raise ValueError(f"{self.scope.value} profile requires organization_id")
        if self.scope is RuntimeProfileScope.PERSONAL and self.user_id is None:
            raise ValueError("PERSONAL profile requires user_id")
        return self

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    @property
    def harness_key(self) -> str | None:
        """Which coding agent an Agent Host profile was made from (``codex``,
        ``claude-code``), as its harness published it; None for a provider."""
        key = self.metadata.get(HARNESS_KEY)
        return key if isinstance(key, str) and key else None

    @property
    def carries_organization_default_mark(self) -> bool:
        """Whether the organization's default mark is stored on this profile,
        eligible or not (`organization_default.can_be_organization_default`)."""
        return isinstance(self.metadata.get(ORGANIZATION_DEFAULT_KEY), dict)

    def derived_harness_kind(self) -> HarnessKind:
        if self.protocol in MODEL_PROVIDER_PROTOCOLS:
            return HarnessKind.LEMMA
        if self.protocol is RuntimeProfileProtocol.AGENT_HOST:
            return HarnessKind.HARNESS
        raise ValueError(f"Unsupported runtime profile protocol: {self.protocol}")

    def with_changes(self, **overrides: Any) -> AgentRuntimeProfile:
        """A re-validated copy carrying ``overrides``.

        ``model_copy`` skips validators, and the validators are exactly what an
        edit must not be allowed to skip - they enforce the harness/kind pairing
        and that a default model exists in the catalog.

        The dump is deliberately in python mode: ``mode="json"`` would render a
        ``SecretStr`` credential as its mask, and this copy is what gets
        persisted, so a rename would quietly overwrite the stored API key with
        asterisks.

        Raises `RuntimeProfileValidationError` -- a 400 naming the field -- for
        a change the profile cannot take.
        """
        return checked_profile(**{**self.model_dump(), **overrides})

    def public_dict(self) -> dict[str, Any]:
        """The profile as the API shows it.

        ``metadata`` is internal bookkeeping (where a profile came from, whether
        its catalog was discovered) and is not published; the two facts a
        client reads out of it are fields of their own.
        """
        data = self.model_dump(
            mode="json", exclude={"credentials", "version", "metadata"}
        )
        data["harness_key"] = self.harness_key
        data["organization_default"] = self.carries_organization_default_mark
        if self.scope is RuntimeProfileScope.SYSTEM:
            data["config"] = {}
            for model in data.get("model_catalog", []):
                if isinstance(model, dict):
                    model["provider_model_name"] = model.get("name")
        elif isinstance(self.config, HarnessRuntimeConfig):
            # A harness config is a revision string, an enumerated-selections
            # map, and a timeout - no Lemma-held secret. Redacting it would
            # rewrite any selection whose key happened to contain "auth" or
            # "token" as the literal mask, and an editor that prefills from this
            # response and sends it back would then persist that mask as the
            # selected value.
            data["config"] = data.get("config", {})
        else:
            data["config"] = public_provider_config(data.get("config", {}))
        data["has_credentials"] = self.has_credentials
        data["derived_harness_kind"] = self.derived_harness_kind().value
        return data

    def _validate_default_model_name(self) -> None:
        if not self.default_model_name:
            return
        catalog_names: set[str] = set()
        for entry in self.model_catalog:
            catalog_names.add(entry.name)
        if catalog_names and self.default_model_name not in catalog_names:
            raise ValueError("default_model_name must match a model catalog name")


def checked_profile(**fields: object) -> AgentRuntimeProfile:
    """A profile built from what a person asked for, or the 400 saying why not.

    For creates and edits. A stored row is read with ``model_validate`` and
    its own failure handling: a bad row is not something the reader can fix.
    """
    try:
        return AgentRuntimeProfile.model_validate(fields)
    except ValidationError as error:
        raise invalid_profile(error) from None


def with_reasoning_declared(
    catalog: list[RuntimeModelCatalogEntry], reasoning_model_names: Collection[str]
) -> list[RuntimeModelCatalogEntry]:
    """``catalog`` with exactly the named models marked ``REASONING``.

    Declared by the person adding the provider, as with vision: a
    ``/models`` listing does not say which models reason, and the mark is what
    lets a reasoning effort reach the wire at all.
    """
    names = {name.strip() for name in reasoning_model_names if name.strip()}
    declared: list[RuntimeModelCatalogEntry] = []
    for entry in catalog:
        others = [
            capability
            for capability in entry.capabilities
            if capability is not RuntimeModelCapability.REASONING
        ]
        capabilities = (
            [*others, RuntimeModelCapability.REASONING]
            if entry.name in names
            else others
        )
        declared.append(entry.model_copy(update={"capabilities": capabilities}))
    return declared


def declared_reasoning_models(catalog: list[RuntimeModelCatalogEntry]) -> set[str]:
    return {entry.name for entry in catalog if entry.reasons}


REDACTED_VALUE = "<redacted>"

# What any member may read of a provider's config. An allowlist, because the
# key-name denylist it replaces passed every secret whose name it did not
# guess -- a key in a base URL's query, a password in its userinfo, a gateway
# subscription header -- while masking `max_tokens` for containing "token".
_PUBLIC_URL_KEYS = ("base_url",)


def public_url(url: str) -> str:
    """``url`` with its userinfo, query and fragment removed."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def public_provider_config(config: Mapping[str, object]) -> JsonObject:
    """A provider config as anyone in the organization may see it.

    Header values are write-only, like the API key: they are as often a
    credential, so only their names are shown. ``model_settings`` is tuning,
    not a secret store, and is returned as stored -- a masked value read back
    and saved again would break every run on the profile.
    """
    public: JsonObject = {}
    for key in _PUBLIC_URL_KEYS:
        if key in config:
            value = config[key]
            public[key] = public_url(str(value)) if value else value
    headers = config.get("headers")
    if isinstance(headers, Mapping):
        public["header_names"] = sorted(str(name) for name in headers)
    model_settings = config.get("model_settings")
    if isinstance(model_settings, Mapping):
        public["model_settings"] = dict(model_settings)
    return public


def refuse_redacted_values(value: object, *, field: str) -> None:
    """Refuse a write carrying the mask an older listing showed for a secret.

    A client that prefilled an edit from that listing sends the mask back, and
    saving it would replace a working value with the literal string.
    """
    if isinstance(value, str):
        if value == REDACTED_VALUE:
            raise RuntimeProfileValidationError(
                f"{field} contains {REDACTED_VALUE!r}; send the real value or "
                "leave the field out to keep the stored one"
            )
    elif isinstance(value, Mapping):
        for item in value.values():
            refuse_redacted_values(item, field=field)
    elif isinstance(value, list):
        for item in value:
            refuse_redacted_values(item, field=field)
