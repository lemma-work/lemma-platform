from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.agent_host import (
    agent_host_harnesses_list,
    agent_host_list,
    agent_host_pairing_create,
    agent_host_revoke,
)
from ..openapi_client.models.agent_host_harness_list_response import (
    AgentHostHarnessListResponse,
)
from ..openapi_client.models.agent_host_list_response import AgentHostListResponse
from ..openapi_client.models.agent_host_pairing_create import AgentHostPairingCreate
from ..openapi_client.models.agent_host_pairing_created import AgentHostPairingCreated
from ..openapi_client.models.agent_host_response import AgentHostResponse
from .base import Resource


class AgentHosts(Resource):
    """Machines paired to run local coding agents for this user."""

    def create_pairing(self, *, display_name: str) -> AgentHostPairingCreated:
        """Mint a short-lived pairing code.

        The code is returned once and is consumed by the Agent Host to obtain
        its own scoped credential.

        There is no organization argument: a paired machine belongs to the user,
        not to a workspace. Sharing it happens later, by giving a runtime profile
        ORGANIZATION scope.
        """
        return self._call(
            agent_host_pairing_create,
            body=AgentHostPairingCreate(display_name=display_name),
        )

    def list(self) -> AgentHostListResponse:
        return self._call(agent_host_list)

    def harnesses(self, host_id: str | UUID) -> AgentHostHarnessListResponse:
        """List the harnesses a host reported, with the ids profiles bind to."""
        return self._call(agent_host_harnesses_list, UUID(str(host_id)))

    def revoke(self, host_id: str | UUID) -> AgentHostResponse:
        return self._call(agent_host_revoke, UUID(str(host_id)))
