from __future__ import annotations

from ..openapi_client.api.agent_conversations import (
    agent_conversation_approval_list,
    agent_conversation_approval_resolve,
    agent_conversation_create,
    agent_conversation_get,
    agent_conversation_list,
    agent_conversation_message_list,
    agent_conversation_message_send,
    agent_conversation_stream,
    agent_conversation_stop,
    agent_conversation_update,
)
from ..openapi_client.models.approval_decision_response import ApprovalDecisionResponse
from ..openapi_client.models.conversation_list_response import ConversationListResponse
from ..openapi_client.models.conversation_response import ConversationResponse
from ..openapi_client.models.conversation_status import ConversationStatus
from ..openapi_client.models.conversation_type import ConversationType
from ..openapi_client.models.create_conversation_request import CreateConversationRequest
from ..openapi_client.models.message_list_response import MessageListResponse
from ..openapi_client.models.resolve_user_approval_request import (
    ResolveUserApprovalRequest,
)
from ..openapi_client.models.send_message_request import SendMessageRequest
from ..openapi_client.models.update_conversation_request import UpdateConversationRequest
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
        return self._call(agent_conversation_get, self._pod_uuid(), as_uuid(conversation_id))

    def update(self, conversation_id: str, request: UpdateConversationRequest) -> ConversationResponse:
        return self._call(
            agent_conversation_update,
            self._pod_uuid(),
            as_uuid(conversation_id),
            body=request,
        )

    def messages(self, conversation_id: str, *, limit: int = 100) -> MessageListResponse:
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
        return self._call(
            agent_conversation_message_send,
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
    ):
        body = SendMessageRequest.from_dict(compact({"content": content, "metadata": metadata}))
        kwargs = agent_conversation_message_send._get_kwargs(
            self._pod_uuid(),
            as_uuid(conversation_id),
            body=body,
        )
        httpx_client = self.generated.get_httpx_client()
        response = httpx_client.send(httpx_client.build_request(**kwargs), stream=True)
        if response.status_code >= 400:
            content_bytes = response.read()
            response.close()
            raise self._transport._error_from_response(response.status_code, None, content_bytes)
        return response

    def stream(self, conversation_id: str, *, agent_run_id: str | None = None):
        kwargs = agent_conversation_stream._get_kwargs(
            self._pod_uuid(),
            as_uuid(conversation_id),
            agent_run_id=as_uuid(agent_run_id) if agent_run_id else UNSET,
        )
        httpx_client = self.generated.get_httpx_client()
        response = httpx_client.send(httpx_client.build_request(**kwargs), stream=True)
        if response.status_code >= 400:
            content_bytes = response.read()
            response.close()
            raise self._transport._error_from_response(response.status_code, None, content_bytes)
        return response

    def stop(self, conversation_id: str) -> ConversationResponse:
        return self._call(agent_conversation_stop, self._pod_uuid(), as_uuid(conversation_id))

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
