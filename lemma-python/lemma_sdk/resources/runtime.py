from __future__ import annotations

from typing import Any

from ..openapi_client.api.agent_runtime import (
    agent_runtime_harnesses_list,
    agent_runtime_profiles_create,
    agent_runtime_profiles_list,
)
from ..openapi_client.models.agent_harness_list_response import AgentHarnessListResponse
from ..openapi_client.models.agent_runtime_profile_list_response import (
    AgentRuntimeProfileListResponse,
)
from ..openapi_client.models.agent_runtime_profile_response import (
    AgentRuntimeProfileResponse,
)
from ..openapi_client.models.create_anthropic_compatible_runtime_profile_request import (
    CreateAnthropicCompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.create_agent_host_runtime_profile_request import (
    CreateAgentHostRuntimeProfileRequest,
)
from ..openapi_client.models.create_open_ai_compatible_runtime_profile_request import (
    CreateOpenAICompatibleRuntimeProfileRequest,
)
from ..openapi_client.models.create_user_daemon_runtime_profile_request import (
    CreateUserDaemonRuntimeProfileRequest,
)
from .base import BoundResource, Resource

_CREATE_MODELS = {
    "AGENT_HOST": CreateAgentHostRuntimeProfileRequest,
    "USER_DAEMON": CreateUserDaemonRuntimeProfileRequest,
    "OPENAI_COMPATIBLE": CreateOpenAICompatibleRuntimeProfileRequest,
    "ANTHROPIC_COMPATIBLE": CreateAnthropicCompatibleRuntimeProfileRequest,
}


def _profile_request(payload: Any):  # type: ignore[no-untyped-def]
    if not isinstance(payload, dict):
        return payload
    source = str(payload.get("source") or "").upper()
    model = _CREATE_MODELS.get(source)
    if model is None:
        raise ValueError(
            "Runtime profile payload needs a 'source' of "
            f"{', '.join(sorted(_CREATE_MODELS))}."
        )
    return model.from_dict(payload)


class Runtime(Resource):
    def harnesses(self) -> AgentHarnessListResponse:
        return self._call(agent_runtime_harnesses_list)


class BoundOrgRuntime(BoundResource):
    def profiles(self) -> AgentRuntimeProfileListResponse:
        return self._call(agent_runtime_profiles_list, self._org_uuid())

    def create_profile(self, request: Any) -> AgentRuntimeProfileResponse:
        return self._call(
            agent_runtime_profiles_create,
            self._org_uuid(),
            body=_profile_request(request),
        )
