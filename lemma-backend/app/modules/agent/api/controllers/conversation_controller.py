"""Pod-scoped agent conversation routes."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.dependencies import PodContextDep
from app.core.domain.errors import BadRequestError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.api.controllers.shared import (
    ChannelServiceDep,
    conversation_channel,
    encode_stream_chunk,
    iter_subscription,
)
from app.modules.agent.api.dependencies import (
    ConversationServiceDep,
)
from app.modules.agent.api.schemas import (
    ApprovalDecisionResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    MessageListResponse,
    MessageResponse,
    ResolveUserApprovalRequest,
    SendMessageRequest,
    UpdateConversationRequest,
    UserApprovalListResponse,
)
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    ConversationType,
    JsonObject,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.pod.services.authorization_factory import create_authorization_service
from app.modules.usage.services.usage_service_factory import build_usage_service
from app.modules.usage.domain.errors import UsageLimitExceededError

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)


def _build_conversation_service(uow) -> ConversationService:
    return ConversationService(
        uow=uow,
        conversation_repository=ConversationRepository(uow),
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_service(uow),
        usage_service=build_usage_service(uow),
    )


def _parse_metadata_filters(
    *,
    query_params: Iterable[tuple[str, str]],
) -> JsonObject | None:
    filters: JsonObject = {}
    for raw_key, value in query_params:
        if not raw_key.startswith("metadata."):
            continue
        key = raw_key.removeprefix("metadata.").strip()
        if not key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Metadata filters must use metadata.<key>=value format.",
        )
        filters[key] = value
    return filters or None


def _parse_message_page_token(page_token: str | None) -> int | None:
    if page_token is None:
        return None
    try:
        value = int(page_token)
    except ValueError as exc:
        raise BadRequestError("Invalid page_token") from exc
    if value < 0:
        raise BadRequestError("Invalid page_token")
    return value


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="agent.conversation.create",
    summary="Create Pod Agent Conversation",
    description=(
        "Create a new pod-scoped conversation. When agent_name is omitted, "
        "the conversation uses the default pod assistant. Workflow and "
        "sub-agent executions also use conversations as their external "
        "execution handle."
    ),
)
async def create_conversation(
    pod_id: UUID,
    data: CreateConversationRequest,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
) -> ConversationResponse:
    _ = ctx
    conversation = await service.create_conversation(
        pod_id=pod_id,
        agent_name=data.agent_name,
        user_id=user.id,
        title=data.title,
        instructions=data.instructions,
        agent_runtime=data.agent_runtime,
        parent_id=data.parent_id,
        type=data.type,
        metadata=data.metadata,
    )
    return ConversationResponse.model_validate(conversation)


@router.get(
    "",
    response_model=ConversationListResponse,
    operation_id="agent.conversation.list",
    summary="List Pod Agent Conversations",
    description=(
        "List root conversations for the current user in a pod. Use "
        "agent_name to list conversations for a specific pod agent; omit it "
        "to list default pod assistant conversations. Child (sub-agent) "
        "conversations are omitted by default; pass parent_id to list the "
        "children of a specific conversation instead."
    ),
)
async def list_conversations(
    pod_id: UUID,
    request: Request,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
    agent_name: str | None = Query(default=None),
    run_status: ConversationStatus | None = Query(default=None, alias="status"),
    conversation_type: ConversationType | None = Query(default=None, alias="type"),
    parent_id: UUID | None = Query(default=None),
    page_token: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> ConversationListResponse:
    _ = ctx
    conversations, next_cursor = await service.list_conversations(
        pod_id=pod_id,
        agent_name=agent_name,
        user_id=user.id,
        status=run_status,
        type=conversation_type,
        metadata_filters=_parse_metadata_filters(
            query_params=request.query_params.multi_items(),
        ),
        parent_id=parent_id,
        cursor=parse_uuid_page_token(page_token),
        limit=limit,
    )
    return ConversationListResponse(
        items=[ConversationResponse.model_validate(item) for item in conversations],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    operation_id="agent.conversation.get",
    summary="Get Pod Conversation",
    description="Get a single pod-scoped assistant or agent conversation by id.",
)
async def get_conversation(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
) -> ConversationResponse:
    _ = ctx
    conversation = await service.get_conversation(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
    )
    return ConversationResponse.model_validate(conversation)


@router.patch(
    "/{conversation_id}",
    response_model=ConversationResponse,
    operation_id="agent.conversation.update",
    summary="Update Pod Conversation",
    description=(
        "Update mutable conversation settings for a pod-scoped conversation. "
        "The conversation runtime is used by future runs; message sends do not "
        "carry per-request runtime overrides."
    ),
)
async def update_conversation(
    pod_id: UUID,
    conversation_id: UUID,
    data: UpdateConversationRequest,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
) -> ConversationResponse:
    _ = ctx
    update_payload = data.model_dump(exclude_unset=True)
    if "agent_runtime" in update_payload:
        update_payload["agent_runtime"] = data.agent_runtime
    conversation = await service.update_conversation(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
        **update_payload,
    )
    return ConversationResponse.model_validate(conversation)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessageListResponse,
    operation_id="agent.conversation.message.list",
    summary="List Pod Conversation Messages",
    description=(
        "List the latest persisted messages in chronological order. Pass "
        "next_page_token as page_token to fetch the next older page above "
        "the current page."
    ),
)
async def list_messages(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
    page_token: str | None = Query(default=None),
    before_sequence: int | None = Query(default=None, ge=0),
    after_sequence: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> MessageListResponse:
    _ = ctx
    token_sequence = _parse_message_page_token(page_token)
    messages, next_cursor = await service.list_messages(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
        before_sequence=token_sequence if token_sequence is not None else before_sequence,
        after_sequence=after_sequence,
        limit=limit,
    )
    return MessageListResponse(
        items=[MessageResponse.model_validate(item) for item in messages],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor is not None else None,
    )


@router.get(
    "/{conversation_id}/approvals",
    response_model=UserApprovalListResponse,
    operation_id="agent.conversation.approval.list",
    summary="List Agent Run Approvals",
    description=(
        "List pending user-interaction tool calls (request_approval and ask_user) "
        "awaiting the user in a conversation."
    ),
)
async def list_approvals(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
) -> UserApprovalListResponse:
    _ = ctx
    approvals = await service.list_user_approvals(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
    )
    return UserApprovalListResponse(
        items=[MessageResponse.model_validate(item) for item in approvals]
    )


@router.post(
    "/{conversation_id}/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    operation_id="agent.conversation.approval.resolve",
    summary="Resolve User Approval",
    description=(
        "Record the user's decision/answers for a paused request_approval or "
        "ask_user call and start a fresh run that resumes the agent. For an "
        "approved request_approval the wrapped tool runs as the user; the "
        "response body carries ask_user answers under `response.answers`."
    ),
)
async def resolve_approval(
    pod_id: UUID,
    conversation_id: UUID,
    approval_id: str,
    data: ResolveUserApprovalRequest,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
) -> ApprovalDecisionResponse:
    _ = ctx
    try:
        await service.resolve_user_approval(
            conversation_id=conversation_id,
            approval_id=approval_id,
            user_id=user.id,
            pod_id=pod_id,
            decision=data.decision,
            response=data.response,
        )
    except RuntimeError as exc:
        # Deliberate remap: the service raises RuntimeError when the approval is
        # already resolved / the run is no longer waiting (a conflict, not a 500).
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ApprovalDecisionResponse(approval_id=approval_id, decision=data.decision)


@router.post(
    "/{conversation_id}/messages",
    response_class=StreamingResponse,
    operation_id="agent.conversation.message.send",
    summary="Send Pod Conversation Message",
    description=(
        "Append a user message to a pod-scoped conversation and stream runtime "
        "events over Server-Sent Events until the active run completes. User "
        "messages can also be appended while a run is already active; the "
        "next harness step sees the new message in persisted history."
    ),
)
async def send_message(
    pod_id: UUID,
    conversation_id: UUID,
    data: SendMessageRequest,
    user: CurrentUser,
    channel_service: ChannelServiceDep,
    ctx: PodContextDep,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> StreamingResponse:
    _ = ctx
    async def close_subscription(
        exc_type=None,
        exc=None,
        traceback=None,
    ) -> None:
        try:
            await subscription.__aexit__(exc_type, exc, traceback)
        except Exception:
            return

    subscription = channel_service.subscribe([conversation_channel(conversation_id)])
    iterator = await subscription.__aenter__()
    try:
        async with uow_factory() as uow:
            service = _build_conversation_service(uow)
            result = await service.add_user_message_and_start_run(
                conversation_id=conversation_id,
                user_id=user.id,
                content=data.content,
                pod_id=pod_id,
                message_metadata=data.metadata,
            )
    except (AgentNotFoundError, ConversationNotFoundError) as exc:
        await close_subscription(type(exc), exc, exc.__traceback__)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    except UsageLimitExceededError as exc:
        await close_subscription(type(exc), exc, exc.__traceback__)
        raise
    except Exception as exc:
        await close_subscription(type(exc), exc, exc.__traceback__)
        raise

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for chunk in iter_subscription(iterator, result.agent_run_id):
                yield chunk
        except Exception as exc:
            yield encode_stream_chunk(
                event_type="error",
                data=str(exc),
                agent_run_id=result.agent_run_id,
            )
        finally:
            await close_subscription()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get(
    "/{conversation_id}/stream",
    response_class=StreamingResponse,
    operation_id="agent.conversation.stream",
    summary="Stream Pod Conversation",
    description=(
        "Subscribe to Server-Sent Events for an existing pod-scoped "
        "conversation. The stream closes immediately when the conversation "
        "has no active run. Optionally filter to a specific internal run id "
        "for reconnects."
    ),
)
async def stream_conversation(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    channel_service: ChannelServiceDep,
    ctx: PodContextDep,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    agent_run_id: UUID | None = Query(default=None),
) -> StreamingResponse:
    _ = ctx
    try:
        async with uow_factory() as uow:
            service = _build_conversation_service(uow)
            await service.get_conversation(
                conversation_id=conversation_id,
                user_id=user.id,
                pod_id=pod_id,
            )
    except (AgentNotFoundError, ConversationNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc

    async def event_generator() -> AsyncGenerator[str, None]:
        async with channel_service.subscribe(
            [conversation_channel(conversation_id)]
        ) as iterator:
            async with uow_factory() as uow:
                service = _build_conversation_service(uow)
                active_run = await service.get_active_agent_run(
                    conversation_id=conversation_id,
                    user_id=user.id,
                    pod_id=pod_id,
                )
            if active_run is None:
                return

            stream_agent_run_id = agent_run_id or active_run.id
            if agent_run_id is not None and agent_run_id != active_run.id:
                return

            async for chunk in iter_subscription(iterator, stream_agent_run_id):
                yield chunk

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post(
    "/{conversation_id}/stop",
    response_model=ConversationResponse,
    operation_id="agent.conversation.stop",
    summary="Stop Pod Conversation",
    description="Request cancellation of the active internal run for a conversation.",
)
async def stop_conversation(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
    ctx: PodContextDep,
) -> ConversationResponse:
    _ = ctx
    conversation = await service.stop_conversation(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
    )
    return ConversationResponse.model_validate(conversation)
