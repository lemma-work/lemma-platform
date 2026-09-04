from __future__ import annotations

import httpx

from ..openapi_client.api.agent_conversations import (
    agent_conversation_approval_list,
    agent_conversation_approval_resolve,
    agent_conversation_create,
    agent_conversation_get,
    agent_conversation_list,
    agent_conversation_message_append,
    agent_conversation_message_list,
    agent_conversation_message_send,
    agent_conversation_retry,
    agent_conversation_stream,
    agent_conversation_stop,
    agent_conversation_update,
)
from ..openapi_client.models.approval_decision_response import ApprovalDecisionResponse
from ..openapi_client.models.agent_run_start_response import AgentRunStartResponse
from ..openapi_client.models.conversation_list_response import ConversationListResponse
from ..openapi_client.models.conversation_response import ConversationResponse
from ..openapi_client.models.conversation_status import ConversationStatus
from ..openapi_client.models.conversation_type import ConversationType
from ..openapi_client.models.create_conversation_request import (
    CreateConversationRequest,
)
from ..openapi_client.models.message_list_response import MessageListResponse
from ..openapi_client.models.resolve_user_approval_request import (
    ResolveUserApprovalRequest,
)
from ..openapi_client.models.send_message_request import SendMessageRequest
from ..openapi_client.models.update_conversation_request import (
    UpdateConversationRequest,
)
from ..openapi_client.models.user_approval_list_response import UserApprovalListResponse
from ..openapi_client.types import UNSET
from ..types import Metadata
from .base import BoundResource, as_uuid, compact

POD_DEFAULT_AGENT_SELECTOR = "POD_DEFAULT"


class PodConversations(BoundResource):
    def list(
        self,
        *,
        agent_name: str | None = None,
        parent_id: str | None = None,
        type: ConversationType | str | None = None,
        status: ConversationStatus | str | None = None,
        limit: int = 20,
    ) -> ConversationListResponse:
        # Root conversations only by default; pass parent_id to fetch a
        # conversation's children (sub-agents or conversations pinned under a
        # PROJECT). `type` filters by CHAT / TASK / PROJECT and composes with
        # parent_id.
        return self._call(
            agent_conversation_list,
            self._pod_uuid(),
            agent_name=agent_name if agent_name is not None else UNSET,
            parent_id=as_uuid(parent_id) if parent_id is not None else UNSET,
            type_=type if type is not None else UNSET,
            status=status if status is not None else UNSET,
            limit=limit,
        )

    def list_default(
        self,
        *,
        parent_id: str | None = None,
        type: ConversationType | str | None = None,
        status: ConversationStatus | str | None = None,
        limit: int = 20,
    ) -> ConversationListResponse:
        return self.list(
            agent_name=POD_DEFAULT_AGENT_SELECTOR,
            parent_id=parent_id,
            type=type,
            status=status,
            limit=limit,
        )

    def create(self, request: CreateConversationRequest) -> ConversationResponse:
        return self._call(agent_conversation_create, self._pod_uuid(), body=request)

    def create_for_agent(
        self,
        agent_name: str,
        *,
        title: str | None = None,
        metadata: Metadata | None = None,
        parent_id: str | None = None,
    ) -> ConversationResponse:
        return self._call(
            agent_conversation_create,
            self._pod_uuid(),
            body=compact(
                {
                    "agent_name": agent_name,
                    "title": title,
                    "metadata": metadata,
                    "parent_id": parent_id,
                }
            ),
            body_model=CreateConversationRequest,
        )

    def get(self, conversation_id: str) -> ConversationResponse:
        return self._call(
            agent_conversation_get, self._pod_uuid(), as_uuid(conversation_id)
        )

    def update(
        self, conversation_id: str, request: UpdateConversationRequest
    ) -> ConversationResponse:
        return self._call(
            agent_conversation_update,
            self._pod_uuid(),
            as_uuid(conversation_id),
            body=request,
        )

    def messages(
        self, conversation_id: str, *, limit: int = 100
    ) -> MessageListResponse:
        return self._call(
            agent_conversation_message_list,
            self._pod_uuid(),
            as_uuid(conversation_id),
            limit=limit,
        )

    def send(
        self,
        conversation_id: str,
        content: str,
        *,
        metadata: Metadata | None = None,
    ) -> None:
        """Send a message and return once the run it starts has finished.

        The endpoint answers with a Server-Sent Events stream that stays open
        for the whole run, so this drains the events and discards them; how long
        an agent thinks is the server's business, not a client deadline. Use
        :meth:`send_stream` to read the events as they arrive, or :meth:`append`
        to post the message and return without waiting.
        """
        response = self.send_stream(conversation_id, content, metadata=metadata)
        try:
            for _ in response.iter_bytes():
                pass
        finally:
            response.close()

    def append(
        self,
        conversation_id: str,
        content: str,
        *,
        metadata: Metadata | None = None,
    ) -> AgentRunStartResponse:
        # Unlike send()/send_stream(), this never opens an SSE stream: it
        # persists the message and returns immediately. When a run is
        # already active it joins that run (steered into the harness's next
        # step) rather than starting a second one; use it for a follow-up
        # message sent while a run is in flight instead of calling send()
        # again, which would open a duplicate stream for the same run.
        return self._call(
            agent_conversation_message_append,
            self._pod_uuid(),
            as_uuid(conversation_id),
            body=compact({"content": content, "metadata": metadata}),
            body_model=SendMessageRequest,
        )

    def send_stream(
        self,
        conversation_id: str,
        content: str,
        *,
        metadata: Metadata | None = None,
    ) -> httpx.Response:
        """Send a message and return the live SSE response for the run.

        The caller iterates the frames and calls ``close()``. Not retried on a
        gateway error: a replay would start a second run.
        """
        return self._stream(
            agent_conversation_message_send,
            self._pod_uuid(),
            as_uuid(conversation_id),
            body=SendMessageRequest.from_dict(
                compact({"content": content, "metadata": metadata})
            ),
        )

    def stream(
        self, conversation_id: str, *, agent_run_id: str | None = None
    ) -> httpx.Response:
        return self._stream(
            agent_conversation_stream,
            self._pod_uuid(),
            as_uuid(conversation_id),
            agent_run_id=as_uuid(agent_run_id) if agent_run_id else UNSET,
        )

    def retry(self, conversation_id: str) -> AgentRunStartResponse:
        return self._call(
            agent_conversation_retry,
            self._pod_uuid(),
            as_uuid(conversation_id),
        )

    def retry_stream(self, conversation_id: str) -> httpx.Response:
        result = self.retry(conversation_id)
        return self.stream(conversation_id, agent_run_id=str(result.agent_run_id))

    def stop(self, conversation_id: str) -> ConversationResponse:
        return self._call(
            agent_conversation_stop, self._pod_uuid(), as_uuid(conversation_id)
        )

    def approvals(self, conversation_id: str) -> UserApprovalListResponse:
        return self._call(
            agent_conversation_approval_list,
            self._pod_uuid(),
            as_uuid(conversation_id),
        )

    def resolve_approval(
        self,
        conversation_id: str,
        approval_id: str,
        request: ResolveUserApprovalRequest | dict,
    ) -> ApprovalDecisionResponse:
        return self._call(
            agent_conversation_approval_resolve,
            self._pod_uuid(),
            as_uuid(conversation_id),
            approval_id,
            body=request,
            body_model=ResolveUserApprovalRequest,
        )
