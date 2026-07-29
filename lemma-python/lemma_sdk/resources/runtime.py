from __future__ import annotations

from typing import Any

from ..openapi_client.api.runtime import (
    runtime_profiles_create,
    runtime_profiles_delete,
    runtime_profiles_get,
    runtime_profiles_list,
    runtime_profiles_refresh,
    runtime_profiles_update,
)
from ..openapi_client.models.create_anthropic_compatible_runtime_profile_request import (
    CreateAnthropicCompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.create_azure_open_ai_runtime_profile_request import (
    CreateAzureOpenAIRuntimeProfileRequest,
)
from ..openapi_client.models.create_google_vertex_runtime_profile_request import (
    CreateGoogleVertexRuntimeProfileRequest,
)
from ..openapi_client.models.create_harness_runtime_profile_request import (
    CreateHarnessRuntimeProfileRequest,
)
from ..openapi_client.models.create_open_ai_compatible_runtime_profile_request import (
    CreateOpenAICompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.update_runtime_profile_request import (
    UpdateRuntimeProfileRequest,
)
from .base import BoundResource, Resource

_CREATE_MODELS = {
    "HARNESS": CreateHarnessRuntimeProfileRequest,
    "OPENAI_COMPATIBLE": CreateOpenAICompatibleRuntimeProfileRequest,
    "ANTHROPIC_COMPATIBLE": CreateAnthropicCompatibleRuntimeProfileRequest,
    "AZURE_OPENAI": CreateAzureOpenAIRuntimeProfileRequest,
    "GOOGLE_VERTEX": CreateGoogleVertexRuntimeProfileRequest,
}


def _profile_request(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    runtime_type = str(payload.get("runtime_type") or "").upper()
    model = _CREATE_MODELS.get(runtime_type)
    if model is None:
        raise ValueError(
            "Runtime profile payload needs a 'runtime_type' of "
            f"{', '.join(sorted(_CREATE_MODELS))}."
        )
    return model.from_dict(payload)


class Runtime(Resource):
    """Root runtime namespace; profile APIs are organization-bound."""


class BoundOrgRuntime(BoundResource):
    def profiles(self) -> Any:
        return self._call(runtime_profiles_list, self._org_uuid())

    def get_profile(self, profile_id: str) -> Any:
        return self._call(
            runtime_profiles_get,
            self._org_uuid(),
            profile_id,
        )

    def create_profile(self, request: Any) -> Any:
        return self._call(
            runtime_profiles_create,
            self._org_uuid(),
            body=_profile_request(request),
        )

    def update_profile(self, profile_id: str, request: Any) -> Any:
        return self._call(
            runtime_profiles_update,
            self._org_uuid(),
            profile_id,
            body=request,
            body_model=UpdateRuntimeProfileRequest,
        )

    def refresh_profile(self, profile_id: str) -> Any:
        return self._call(
            runtime_profiles_refresh,
            self._org_uuid(),
            profile_id,
        )

    def delete_profile(self, profile_id: str) -> Any:
        return self._call(
            runtime_profiles_delete,
            self._org_uuid(),
            profile_id,
        )
