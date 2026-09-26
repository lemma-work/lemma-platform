from __future__ import annotations

from typing import Any
from uuid import UUID

from ..openapi_client.api.agent_runtime import (
    agent_runtime_default_clear,
    agent_runtime_default_set,
    agent_runtime_profiles_archive,
    agent_runtime_profiles_create,
    agent_runtime_profiles_get,
    agent_runtime_profiles_list,
    agent_runtime_profiles_restore,
    agent_runtime_profiles_test,
    agent_runtime_profiles_update,
)
from ..openapi_client.models.agent_runtime_config import AgentRuntimeConfig
from ..openapi_client.models.agent_runtime_profile_detail_response import (
    AgentRuntimeProfileDetailResponse,
)
from ..openapi_client.models.agent_runtime_profile_list_response import (
    AgentRuntimeProfileListResponse,
)
from ..openapi_client.models.agent_runtime_profile_response import (
    AgentRuntimeProfileResponse,
)
from ..openapi_client.models.agent_runtime_profile_test_response import (
    AgentRuntimeProfileTestResponse,
)
from ..openapi_client.models.set_organization_default_runtime_request import (
    SetOrganizationDefaultRuntimeRequest,
)
from ..openapi_client.models.create_anthropic_compatible_runtime_profile_request import (
    CreateAnthropicCompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.create_open_ai_compatible_runtime_profile_request import (
    CreateOpenAICompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.create_agent_host_runtime_profile_request import (
    CreateAgentHostRuntimeProfileRequest,
)
from ..openapi_client.models.update_anthropic_compatible_runtime_profile_request import (
    UpdateAnthropicCompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.update_open_ai_compatible_runtime_profile_request import (
    UpdateOpenAICompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.update_agent_host_runtime_profile_request import (
    UpdateAgentHostRuntimeProfileRequest,
)
from .base import BoundResource, as_uuid

_CREATE_MODELS = {
    "AGENT_HOST": CreateAgentHostRuntimeProfileRequest,
    "OPENAI_COMPATIBLE": CreateOpenAICompatibleRuntimeProfileRequest,
    "ANTHROPIC_COMPATIBLE": CreateAnthropicCompatibleRuntimeProfileRequest,
}

_UPDATE_MODELS = {
    "AGENT_HOST": UpdateAgentHostRuntimeProfileRequest,
    "OPENAI_COMPATIBLE": UpdateOpenAICompatibleRuntimeProfileRequest,
    "ANTHROPIC_COMPATIBLE": UpdateAnthropicCompatibleRuntimeProfileRequest,
}


def _typed_request(payload: Any, models: dict[str, Any], verb: str):  # type: ignore[no-untyped-def]
    if not isinstance(payload, dict):
        return payload
    source = str(payload.get("source") or "").upper()
    model = models.get(source)
    if model is None:
        raise ValueError(
            f"Runtime profile {verb} payload needs a 'source' of "
            f"{', '.join(sorted(models))}."
        )
    return model.from_dict(payload)


def _profile_request(payload: Any):  # type: ignore[no-untyped-def]
    return _typed_request(payload, _CREATE_MODELS, "create")


def _profile_update_request(payload: Any):  # type: ignore[no-untyped-def]
    return _typed_request(payload, _UPDATE_MODELS, "update")


class BoundOrgRuntime(BoundResource):
    def profiles(
        self, *, include_disabled: bool = False
    ) -> AgentRuntimeProfileListResponse:
        return self._call(
            agent_runtime_profiles_list,
            self._org_uuid(),
            include_disabled=include_disabled,
        )

    def profile(self, profile_id: str | UUID) -> AgentRuntimeProfileDetailResponse:
        return self._call(
            agent_runtime_profiles_get,
            self._org_uuid(),
            as_uuid(profile_id),
        )

    def create_profile(self, request: Any) -> AgentRuntimeProfileResponse:
        return self._call(
            agent_runtime_profiles_create,
            self._org_uuid(),
            body=_profile_request(request),
        )

    def update_profile(
        self, profile_id: str | UUID, request: Any
    ) -> AgentRuntimeProfileResponse:
        """Patch a profile. Only the keys present in ``request`` are applied, so
        a rename never has to resend the stored API key."""
        return self._call(
            agent_runtime_profiles_update,
            self._org_uuid(),
            as_uuid(profile_id),
            body=_profile_update_request(request),
        )

    def archive_profile(self, profile_id: str | UUID) -> None:
        """Retire a profile from the catalog. Reversible via ``restore_profile``;
        there is no hard delete."""
        return self._call(
            agent_runtime_profiles_archive,
            self._org_uuid(),
            as_uuid(profile_id),
        )

    def restore_profile(self, profile_id: str | UUID) -> AgentRuntimeProfileResponse:
        return self._call(
            agent_runtime_profiles_restore,
            self._org_uuid(),
            as_uuid(profile_id),
        )

    def test_profile(self, profile_id: str | UUID) -> AgentRuntimeProfileTestResponse:
        """List a saved provider's models and send its default model one short
        message. ``ok`` is false with a plain-language ``message`` when the key
        is rejected or the provider does not answer."""
        return self._call(
            agent_runtime_profiles_test,
            self._org_uuid(),
            str(profile_id),
        )

    def set_default(
        self, profile_id: str | UUID, *, model_name: str | None = None
    ) -> AgentRuntimeConfig:
        """Make an organization-wide model provider what every teammate that
        names no model runs on. Without ``model_name`` it follows the
        provider's own default model."""
        body: dict[str, Any] = {"profile_id": str(profile_id)}
        if model_name is not None:
            body["model_name"] = model_name
        return self._call(
            agent_runtime_default_set,
            self._org_uuid(),
            body=SetOrganizationDefaultRuntimeRequest.from_dict(body),
        )

    def clear_default(self) -> None:
        """Stop choosing a model for the organization; teammates fall back to
        the server's model, or the first provider when it has none."""
        return self._call(agent_runtime_default_clear, self._org_uuid())
