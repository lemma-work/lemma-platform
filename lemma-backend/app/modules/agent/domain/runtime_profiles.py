"""Runtime profile domain models for agent execution."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

from app.modules.agent.domain.value_objects import HarnessKind, JsonObject


class RuntimeProfileScope(str, Enum):
    SYSTEM = "SYSTEM"
    ORGANIZATION = "ORGANIZATION"
    PERSONAL = "PERSONAL"


class RuntimeProfileKind(str, Enum):
    MODEL_PROVIDER = "MODEL_PROVIDER"
    HARNESS = "HARNESS"


class RuntimeProfileProtocol(str, Enum):
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    ANTHROPIC_COMPATIBLE = "ANTHROPIC_COMPATIBLE"
    AZURE_OPENAI = "AZURE_OPENAI"
    GOOGLE_VERTEX = "GOOGLE_VERTEX"
    CODEX_APP_SERVER = "CODEX_APP_SERVER"
    CLAUDE_CODE = "CLAUDE_CODE"
    OPENCODE = "OPENCODE"


class RuntimeProfileStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


class RuntimeModelCapability(str, Enum):
    TEXT = "TEXT"
    TOOLS = "TOOLS"
    VISION = "VISION"
    AUDIO = "AUDIO"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    REASONING = "REASONING"


MODEL_PROVIDER_PROTOCOLS = frozenset(
    {
        RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
        RuntimeProfileProtocol.AZURE_OPENAI,
        RuntimeProfileProtocol.GOOGLE_VERTEX,
    }
)

HARNESS_PROTOCOLS = frozenset(
    {
        RuntimeProfileProtocol.CODEX_APP_SERVER,
        RuntimeProfileProtocol.CLAUDE_CODE,
        RuntimeProfileProtocol.OPENCODE,
    }
)


class RuntimeModelCatalogEntry(BaseModel):
    name: str = Field(min_length=1)
    display_name: str | None = None
    provider_model_name: str = Field(min_length=1)
    capabilities: list[RuntimeModelCapability] = Field(default_factory=list)
    default_model_settings: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("name", "provider_model_name")
    @classmethod
    def normalize_required_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized


class ApiKeyRuntimeCredentials(BaseModel):
    # SecretStr so the key never leaks via repr()/logs/tracebacks; read the
    # plaintext only through ``reveal_credentials`` at the point of use.
    api_key: SecretStr = Field(min_length=1)


class OAuthRuntimeCredentials(BaseModel):
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = None
    expires_at: str | None = None


RuntimeCredentials = ApiKeyRuntimeCredentials | OAuthRuntimeCredentials | JsonObject


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


class AzureOpenAIRuntimeConfig(BaseModel):
    azure_endpoint: HttpUrl
    azure_version: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    model_settings: JsonObject = Field(default_factory=dict)


class GoogleVertexRuntimeConfig(BaseModel):
    project_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    model_settings: JsonObject = Field(default_factory=dict)


class CodexAppServerRuntimeConfig(BaseModel):
    endpoint_url: HttpUrl | str | None = None
    binary: str = "codex"


class ClaudeCodeRuntimeConfig(BaseModel):
    endpoint_url: HttpUrl | str | None = None
    binary: str = "claude"


class OpenCodeRuntimeConfig(BaseModel):
    endpoint_url: HttpUrl | str | None = None
    binary: str = "opencode"


RuntimeProfileConfig = (
    OpenAICompatibleRuntimeConfig
    | AnthropicCompatibleRuntimeConfig
    | AzureOpenAIRuntimeConfig
    | GoogleVertexRuntimeConfig
    | CodexAppServerRuntimeConfig
    | ClaudeCodeRuntimeConfig
    | OpenCodeRuntimeConfig
    | JsonObject
)


class AgentRuntimeProfile(BaseModel):
    """Org/system profile that owns model/harness execution configuration."""

    model_config = ConfigDict(use_enum_values=False)

    id: str
    organization_id: UUID | None = None
    user_id: UUID | None = None
    daemon_id: UUID | None = None
    scope: RuntimeProfileScope
    kind: RuntimeProfileKind
    protocol: RuntimeProfileProtocol
    name: str = Field(min_length=1)
    description: str | None = None
    default_model_name: str | None = None
    model_catalog: list[RuntimeModelCatalogEntry] = Field(default_factory=list)
    config: RuntimeProfileConfig = Field(default_factory=dict)
    credentials: RuntimeCredentials | None = None
    status: RuntimeProfileStatus = RuntimeProfileStatus.ACTIVE
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("id", "name")
    @classmethod
    def normalize_non_empty_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized

    @model_validator(mode="after")
    def validate_profile(self) -> "AgentRuntimeProfile":
        if self.kind is RuntimeProfileKind.MODEL_PROVIDER:
            if self.protocol not in MODEL_PROVIDER_PROTOCOLS:
                raise ValueError("MODEL_PROVIDER profile has invalid protocol")
            if not self.default_model_name:
                raise ValueError("MODEL_PROVIDER profile requires default_model_name")
            self._validate_default_model_name()
        if self.kind is RuntimeProfileKind.HARNESS:
            if self.protocol not in HARNESS_PROTOCOLS:
                raise ValueError("HARNESS profile has invalid protocol")
        if (
            self.scope in {RuntimeProfileScope.ORGANIZATION, RuntimeProfileScope.PERSONAL}
            and self.organization_id is None
        ):
            raise ValueError(f"{self.scope.value} profile requires organization_id")
        if self.scope is RuntimeProfileScope.PERSONAL and self.user_id is None:
            raise ValueError("PERSONAL profile requires user_id")
        if self.daemon_id is not None and self.user_id is None:
            raise ValueError("Daemon runtime profile requires user_id")
        return self

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    def derived_harness_kind(self) -> HarnessKind:
        if self.protocol in MODEL_PROVIDER_PROTOCOLS:
            return HarnessKind.LEMMA
        if self.protocol is RuntimeProfileProtocol.CODEX_APP_SERVER:
            return HarnessKind.CODEX
        if self.protocol is RuntimeProfileProtocol.CLAUDE_CODE:
            return HarnessKind.CLAUDE_CODE
        if self.protocol is RuntimeProfileProtocol.OPENCODE:
            return HarnessKind.OPENCODE
        raise ValueError(f"Unsupported runtime profile protocol: {self.protocol}")

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"credentials"})
        if self.scope is RuntimeProfileScope.SYSTEM:
            data["config"] = {}
            for model in data.get("model_catalog", []):
                if isinstance(model, dict):
                    model["provider_model_name"] = model.get("name")
        else:
            data["config"] = _redact_public_secrets(data.get("config", {}))
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


_REDACTED_VALUE = "<redacted>"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
    "x-api-key",
)


def _redact_public_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key_text] = _REDACTED_VALUE
            else:
                redacted[key_text] = _redact_public_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_public_secrets(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part.replace("-", "_") in normalized for part in _SENSITIVE_KEY_PARTS)
