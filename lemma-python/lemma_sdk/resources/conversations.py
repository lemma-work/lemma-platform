from __future__ import annotations

from uuid import uuid4

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
    agent_conversation_message_withdraw,
    agent_conversation_pending_interaction_list,
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
from ..openapi_client.models.pending_interaction_list_response import (
    PendingInteractionListResponse,
)
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


def _message_body(
    content: str, metadata: Metadata | None, client_message_id: str | None
) -> dict[str, object]:
    """A send, carrying the id that makes sending it again safe.

    The server stores one message per ``client_message_id`` per conversation
    and answers a repeat with the first send's run. Pass the same id when
    retrying a message; otherwise one is made for this call.
    """
    return compact(
        {
            "content": content,
            "metadata": metadata,
            "client_message_id": client_message_id or str(uuid4()),
        }
    )


POD_DEFAULT_AGENT_SELECTOR = "POD_DEFAULT"


class PodConversations(BoundResource):
    def list(
        self,
        *,
        agent_name: str | None = None,
        parent_id: str | None = None,
        type: ConversationType | str | None = None,
        status: ConversationStatus | str | None = None,
        search: str | None = None,
        in_channel: bool | None = None,
        limit: int = 20,
    ) -> ConversationListResponse:
        # Root conversations only by default; pass parent_id to fetch a
        # conversation's children (sub-agents or conversations pinned under a
        # PROJECT). `type` filters by CHAT / TASK / PROJECT and composes with
        # parent_id. `search` keeps titles containing it, case-insensitively.
        # `in_channel=False` keeps the conversations of no channel.
        return self._call(
            agent_conversation_list,
            self._pod_uuid(),
            agent_name=agent_name if agent_name is not None else UNSET,
            parent_id=as_uuid(parent_id) if parent_id is not None else UNSET,
            type_=type if type is not None else UNSET,
            status=status if status is not None else UNSET,
            search=search if search is not None else UNSET,
            in_channel=in_channel if in_channel is not None else UNSET,
            limit=limit,
        )

    def list_default(
        self,
        *,
        parent_id: str | None = None,
        type: ConversationType | str | None = None,
        status: ConversationStatus | str | None = None,
        search: str | None = None,
        limit: int = 20,
    ) -> ConversationListResponse:
        return self.list(
            agent_name=POD_DEFAULT_AGENT_SELECTOR,
            parent_id=parent_id,
            type=type,
            status=status,
            search=search,
            limit=limit,
        )

    def create(self, request: CreateConversationRequest) -> ConversationResponse:
        return self._call(agent_conversation_create, self._pod_uuid(), body=request)

    def create_for_agent(
        self,
        agent_name: str | None = None,
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
                    # None or blank: the pod's own assistant, by leaving it out.
                    "agent_name": (agent_name or "").strip() or None,
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
        self,
        conversation_id: str,
        *,
        limit: int = 100,
        after_sequence: int | None = None,
        page_token: str | None = None,
    ) -> MessageListResponse:
        """The newest ``limit`` messages, oldest first; ``after_sequence``
        keeps only later ones, and ``page_token`` reads the page before."""
        return self._call(
            agent_conversation_message_list,
            self._pod_uuid(),
            as_uuid(conversation_id),
            limit=limit,
            after_sequence=UNSET if after_sequence is None else after_sequence,
            page_token=UNSET if page_token is None else page_token,
        )

    def send(
        self,
        conversation_id: str,
        content: str,
        *,
        metadata: Metadata | None = None,
        client_message_id: str | None = None,
    ) -> None:
        """Send a message and return once the run it starts has finished.

        The endpoint answers with a Server-Sent Events stream that stays open
        for the whole run, so this drains the events and discards them; how long
        an agent thinks is the server's business, not a client deadline. Use
        :meth:`send_stream` to read the events as they arrive, or :meth:`append`
        to post the message and return without waiting.
        """
        response = self.send_stream(
            conversation_id,
            content,
            metadata=metadata,
            client_message_id=client_message_id,
        )
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
        client_message_id: str | None = None,
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
            body=_message_body(content, metadata, client_message_id),
            body_model=SendMessageRequest,
        )

    def withdraw(self, conversation_id: str, message_id: str) -> None:
        """Take back a message the agent has not seen yet.

        Only a message sent while a run was working, and only until something
        delivers it; the API answers 409 once a run has read it.
        """
        self._call(
            agent_conversation_message_withdraw,
            self._pod_uuid(),
            as_uuid(conversation_id),
            as_uuid(message_id),
        )

    def send_stream(
        self,
        conversation_id: str,
        content: str,
        *,
        metadata: Metadata | None = None,
        client_message_id: str | None = None,
        read_timeout: float | None = None,
    ) -> httpx.Response:
        """Send a message and return the live SSE response for the run.

        The caller iterates the frames and calls ``close()``. Not retried on a
        gateway error: a replay would start a second run.

        ``read_timeout`` bounds the wait between two reads. The server sends a
        keepalive through a quiet run, so a stream that goes longer than that
        with nothing at all is a dead connection, not a slow agent. ``None``
        waits for ever.
        """
        return self._stream(
            agent_conversation_message_send,
            self._pod_uuid(),
            as_uuid(conversation_id),
            read_timeout=read_timeout,
            body=SendMessageRequest.from_dict(
                _message_body(content, metadata, client_message_id)
            ),
        )

    def stream(
        self,
        conversation_id: str,
        *,
        agent_run_id: str | None = None,
        read_timeout: float | None = None,
    ) -> httpx.Response:
        """Attach to a conversation's run. A run that has already ended
        replays its terminal event and closes; ``read_timeout`` is as for
        ``send_stream``."""
        return self._stream(
            agent_conversation_stream,
            self._pod_uuid(),
            as_uuid(conversation_id),
            read_timeout=read_timeout,
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

    def pending_interactions(
        self, *, page_token: str | None = None, limit: int = 50
    ) -> PendingInteractionListResponse:
        """Questions, approvals and Agent Host permission requests waiting on you.

        Across your own conversations in the pod, longest-waiting first. Answer
        one with `resolve_approval`, passing its `tool_call_id` as the approval
        id.
        """
        return self._call(
            agent_conversation_pending_interaction_list,
            self._pod_uuid(),
            page_token=page_token if page_token is not None else UNSET,
            limit=limit,
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
