from __future__ import annotations

from ..openapi_client.api.agents import (
    agent_create,
    agent_delete,
    agent_get,
    agent_list,
    agent_permissions_get,
    agent_permissions_replace,
    agent_update,
)
from ..openapi_client.models.agent_detail_response import AgentDetailResponse
from ..openapi_client.models.agent_list_response import AgentListResponse
from ..openapi_client.models.agent_permissions_replace_request import (
    AgentPermissionsReplaceRequest,
)
from ..openapi_client.models.agent_permissions_response import AgentPermissionsResponse
from ..openapi_client.models.conversation_response import ConversationResponse
from ..openapi_client.models.create_agent_request import CreateAgentRequest
from ..openapi_client.models.update_agent_request import UpdateAgentRequest
from ..types import Metadata
from .base import BoundResource


class PodAgents(BoundResource):
    def list(
        self, *, limit: int = 100, include: list[str] | None = None
    ) -> AgentListResponse:
        """List agents. Pass ``include=["permissions"]`` to get each agent's
        grants in the same request — the server resolves them for the whole page
        in one query, instead of a per-agent permissions call each."""
        return self._call(
            agent_list, self._pod_uuid(), limit=limit, include=include or []
        )

    def run(
        self,
        name_or_id: str,
        message: str,
        *,
        title: str | None = None,
        metadata: Metadata | None = None,
        stream: bool = False,
    ):
        """Run an agent on a single message (the ``.run`` verb, alongside
        ``functions.run`` / ``queries.run``).

        Note the return contract differs from those: ``functions.run`` and
        ``queries.run`` return the result directly, whereas an agent reply is
        asynchronous, so this opens a fresh conversation, sends ``message``, and
        returns the created :class:`ConversationResponse` — read the reply with
        ``pod.conversations.messages(conv.id)`` (or ``.stream(conv.id)``). With
        ``stream=True`` it returns the raw streaming response so you can consume
        tokens as they arrive.
        """
        # Local import avoids any import-order coupling between resources.
        from .conversations import PodConversations

        conversations = PodConversations(self._transport, pod_id=self.pod_id)
        conversation: ConversationResponse = conversations.create_for_agent(
            name_or_id, title=title, metadata=metadata
        )
        conversation_id = str(conversation.id)
        if stream:
            return conversations.send_stream(
                conversation_id, message, metadata=metadata
            )
        conversations.send(conversation_id, message)
        return conversation

    def create(self, request: CreateAgentRequest) -> AgentDetailResponse:
        return self._call(agent_create, self._pod_uuid(), body=request)

    def get(self, name_or_id: str) -> AgentDetailResponse:
        return self._call(agent_get, self._pod_uuid(), name_or_id)

    def update(
        self, name_or_id: str, request: UpdateAgentRequest
    ) -> AgentDetailResponse:
        return self._call(agent_update, self._pod_uuid(), name_or_id, body=request)

    def delete(self, name_or_id: str) -> None:
        self._call(agent_delete, self._pod_uuid(), name_or_id)

    def permissions(self, name: str) -> AgentPermissionsResponse:
        return self._call(agent_permissions_get, self._pod_uuid(), name)

    def replace_permissions(
        self,
        name: str,
        request: AgentPermissionsReplaceRequest,
    ) -> AgentPermissionsResponse:
        return self._call(
            agent_permissions_replace, self._pod_uuid(), name, body=request
        )
