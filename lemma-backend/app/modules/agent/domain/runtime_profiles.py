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


class RuntimeProfileType(str, Enum):
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    ANTHROPIC_COMPATIBLE = "ANTHROPIC_COMPATIBLE"
    AZURE_OPENAI = "AZURE_OPENAI"
    GOOGLE_VERTEX = "GOOGLE_VERTEX"
    HARNESS = "HARNESS"


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


MODEL_PROVIDER_TYPES = frozenset(
    {
        RuntimeProfileType.OPENAI_COMPATIBLE,
        RuntimeProfileType.ANTHROPIC_COMPATIBLE,
        RuntimeProfileType.AZURE_OPENAI,
        RuntimeProfileType.GOOGLE_VERTEX,
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
    model_config = ConfigDict(extra="forbid")

    # SecretStr so the key never leaks via repr()/logs/tracebacks; read the
    # plaintext only through ``reveal_credentials`` at the point of use.
    api_key: SecretStr = Field(min_length=1)


class GoogleVertexRuntimeCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_account_json: JsonObject


RuntimeCredentials = ApiKeyRuntimeCredentials | GoogleVertexRuntimeCredentials


def reveal_credentials(
    credentials: RuntimeCredentials | None,
) -> dict[str, object] | None:
    """Plaintext dict form of runtime credentials for actual use.

    This is the single place secrets are unwrapped: ``SecretStr`` fields become
    plain strings here, so the result must only flow to a consumer that truly
    needs the value (harness authentication, encrypted persistence) — never to a
    log, response body, or repr. Everywhere else, keep the typed credential
    object, which masks its secrets.
    """
    if credentials is None:
        return None
    return {
        key: (value.get_secret_value() if isinstance(value, SecretStr) else value)
        for key, value in credentials.model_dump().items()
    }


class OpenAICompatibleRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


class AnthropicCompatibleRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


class AzureOpenAIRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    azure_endpoint: HttpUrl
    api_version: str | None = Field(default=None, min_length=1)
    model_settings: JsonObject = Field(default_factory=dict)


class GoogleVertexRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    model_settings: JsonObject = Field(default_factory=dict)


class HarnessRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness_snapshot_revision: str = Field(min_length=1, max_length=255)
    config_selections: JsonObject = Field(default_factory=dict)
    host_wait_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    fallback_profile_id: str | None = Field(default=None, min_length=1)


RuntimeProfileConfig = (
    HarnessRuntimeConfig
    | OpenAICompatibleRuntimeConfig
    | AnthropicCompatibleRuntimeConfig
    | AzureOpenAIRuntimeConfig
    | GoogleVertexRuntimeConfig
)


class AgentRuntimeProfile(BaseModel):
    """Org/system profile that owns model/harness execution configuration."""

    model_config = ConfigDict(use_enum_values=False)

    id: str
    organization_id: UUID | None = None
    owner_user_id: UUID | None = None
    harness_id: UUID | None = None
    scope: RuntimeProfileScope
    runtime_type: RuntimeProfileType
    name: str = Field(min_length=1)
    description: str | None = None
    default_model_name: str | None = None
    model_catalog: list[RuntimeModelCatalogEntry] = Field(default_factory=list)
    config: RuntimeProfileConfig
    credentials: RuntimeCredentials | None = None
    status: RuntimeProfileStatus = RuntimeProfileStatus.ACTIVE

    @field_validator("id", "name")
    @classmethod
    def normalize_non_empty_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized

    @model_validator(mode="after")
    def validate_profile(self) -> "AgentRuntimeProfile":
        if self.runtime_type in MODEL_PROVIDER_TYPES:
            if self.harness_id is not None:
                raise ValueError("Provider runtime profile cannot reference a harness")
            if not self.default_model_name:
                raise ValueError("Provider runtime profile requires default_model_name")
            expected_config = {
                RuntimeProfileType.OPENAI_COMPATIBLE: OpenAICompatibleRuntimeConfig,
                RuntimeProfileType.ANTHROPIC_COMPATIBLE: (
                    AnthropicCompatibleRuntimeConfig
                ),
                RuntimeProfileType.AZURE_OPENAI: AzureOpenAIRuntimeConfig,
                RuntimeProfileType.GOOGLE_VERTEX: GoogleVertexRuntimeConfig,
            }[self.runtime_type]
            if not isinstance(self.config, expected_config):
                raw_config = self.config.model_dump(mode="json")
                self.config = expected_config.model_validate(raw_config)
            expected_credentials = (
                GoogleVertexRuntimeCredentials
                if self.runtime_type is RuntimeProfileType.GOOGLE_VERTEX
                else ApiKeyRuntimeCredentials
            )
            if self.credentials is not None and not isinstance(
                self.credentials, expected_credentials
            ):
                raise ValueError(
                    f"{self.runtime_type.value} profile has incompatible credentials"
                )
            self._validate_default_model_name()
        if self.runtime_type is RuntimeProfileType.HARNESS:
            if self.harness_id is None:
                raise ValueError("HARNESS profile requires harness_id")
            if self.credentials is not None:
                raise ValueError("HARNESS profile cannot contain credentials")
            if not isinstance(self.config, HarnessRuntimeConfig):
                raw_config = (
                    self.config.model_dump(mode="json")
                    if isinstance(self.config, BaseModel)
                    else self.config
                )
                self.config = HarnessRuntimeConfig.model_validate(raw_config)
        if (
            self.scope in {RuntimeProfileScope.ORGANIZATION, RuntimeProfileScope.PERSONAL}
            and self.organization_id is None
        ):
            raise ValueError(f"{self.scope.value} profile requires organization_id")
        if self.scope is RuntimeProfileScope.PERSONAL and self.owner_user_id is None:
            raise ValueError("PERSONAL profile requires owner_user_id")
        if self.scope is RuntimeProfileScope.ORGANIZATION and self.owner_user_id is not None:
            raise ValueError("ORGANIZATION profile cannot have owner_user_id")
        return self

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    def derived_harness_kind(self) -> HarnessKind:
        if self.runtime_type in MODEL_PROVIDER_TYPES:
            return HarnessKind.LEMMA
        if self.runtime_type is RuntimeProfileType.HARNESS:
            return HarnessKind.HARNESS
        raise ValueError(f"Unsupported runtime profile type: {self.runtime_type}")

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"credentials"})
        if self.scope is RuntimeProfileScope.SYSTEM:
            data["config"] = None
            for model in data.get("model_catalog", []):
                if isinstance(model, dict):
                    model["provider_model_name"] = model.get("name")
        else:
            data["config"] = _redact_public_secrets(data.get("config", {}))
        data["has_credentials"] = self.has_credentials
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
