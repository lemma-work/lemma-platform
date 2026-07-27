from __future__ import annotations

from typing import Any

from ..openapi_client.api.agent_host import (
    agent_host_integrations_list,
    agent_host_list,
    agent_host_pairing_create,
    agent_host_revoke,
)
from ..openapi_client.models.agent_host_integration_list_response import (
    AgentHostIntegrationListResponse,
)
from ..openapi_client.models.agent_host_list_response import AgentHostListResponse
from ..openapi_client.models.agent_host_pairing_create import AgentHostPairingCreate
from ..openapi_client.models.agent_host_pairing_created import AgentHostPairingCreated
from ..openapi_client.models.agent_host_response import AgentHostResponse
from .base import Resource, as_uuid


class AgentHosts(Resource):
    """Authenticated management surface for user-owned Agent Hosts.

    Device polling, event append, token exchange, and MCP-route operations are
    intentionally reserved for the native Agent Host binary.
    """

    def list(self) -> AgentHostListResponse:
        return self._call(agent_host_list)

    def create_pairing(
        self,
        request: AgentHostPairingCreate | dict[str, Any],
    ) -> AgentHostPairingCreated:
        return self._call(
            agent_host_pairing_create,
            body=request,
            body_model=AgentHostPairingCreate,
        )

    def integrations(self, host_id: str) -> AgentHostIntegrationListResponse:
        return self._call(agent_host_integrations_list, as_uuid(host_id))

    def revoke(self, host_id: str) -> AgentHostResponse:
        return self._call(agent_host_revoke, as_uuid(host_id))
