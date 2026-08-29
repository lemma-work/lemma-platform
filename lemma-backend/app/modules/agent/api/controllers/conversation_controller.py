"""Pod-scoped agent conversation routes."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.dependencies import (
    PodContextDep,
    assert_pod_membership,
    require_pod_membership,
)
from app.core.authorization.delegation import POD_DEFAULT_AGENT_SELECTOR_ALIASES
from app.core.authorization.scope import pod_context_scope
from app.core.domain.errors import BadRequestError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.api.controllers.conversation_streaming import (
    load_authorized_agent_run,
    start_and_stream_run,
    terminal_run_chunk,
)
from app.modules.agent.api.controllers.shared import (
    ChannelServiceDep,
    conversation_channel,
    iter_subscription,
)
from app.modules.agent.api.dependencies import (
    ConversationServiceDep,
)
from app.modules.agent.api.schemas import (
    AgentRunStartResponse,
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
    AgentRunStartResult,
    ConversationAgentSelection,
    ConversationStatus,
    ConversationType,
    JsonObject,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_retry_service import (
    ConversationRetryService,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.composition.authorization import create_authorization_service
from app.composition.agent_usage import build_usage_service

logger = get_logger(__name__)

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)

_CONVERSATION_ACCESS = "use conversations in this pod"

#: Membership is a precondition of conversation access, not something inferred
#: from grants: ownership plus an agent grant survive membership removal, which
#: let a removed member keep reading -- and instructing -- the pod's agents.
#: See PS-POD-040, PS-ACCESS-023, DEV-ACCESS-001. Applied per route, not on the
#: router: the three routes that build their context in a short
#: ``pod_context_scope`` would be handed a request-scoped ``PodContextDep``
#: anyway, undoing the very thing that scope exists for -- they call
#: ``assert_pod_membership`` inside their own scope instead.
#:
#: It is also what resolves the pod ``Context`` for these handlers now. They used
#: to take a ``PodContextDep`` they never read, purely to make that happen.
CONVERSATION_MEMBERSHIP = require_pod_membership(_CONVERSATION_ACCESS)
CONVERSATION_ENUMERATION = require_pod_membership(_CONVERSATION_ACCESS, enumerates=True)


def _build_conversation_service(uow) -> ConversationService:
    return ConversationService(
        uow=uow,
        conversation_repository=ConversationRepository(uow),
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_service(uow),
        usage_service=build_usage_service(uow),
    )


def _build_conversation_retry_service(uow) -> ConversationRetryService:
    return ConversationRetryService(
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


def _parse_conversation_agent_selection(
    agent_name: str | None,
) -> ConversationAgentSelection[str]:
    if agent_name is None:
        return ConversationAgentSelection.all()
    if not agent_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="agent_name cannot be empty",
        )
    if agent_name in POD_DEFAULT_AGENT_SELECTOR_ALIASES:
        return ConversationAgentSelection.pod_default()
    return ConversationAgentSelection.named(agent_name)


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
    dependencies=[CONVERSATION_MEMBERSHIP],
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
) -> ConversationResponse:
    conversation = await service.create_conversation(
        pod_id=pod_id,
        agent_name=data.agent_name,
        user_id=user.id,
        title=data.title,
        title_is_placeholder=data.title_is_placeholder,
        instructions=data.instructions,
        agent_runtime=data.agent_runtime,
        parent_id=data.parent_id,
        type=data.type,
        metadata=data.metadata,
    )
    response = ConversationResponse.model_validate(conversation)
    if data.title_is_placeholder and data.title:
        # Not persisted (see create_conversation) so title generation still
        # runs, but the caller's own optimistic UI still gets it back once.
        response.title = data.title
    return response


@router.get(
    "",
    response_model=ConversationListResponse,
    operation_id="agent.conversation.list",
    dependencies=[CONVERSATION_ENUMERATION],
    summary="List Pod Agent Conversations",
    description=(
        "List root conversations for the current user in a pod. Omit "
        "agent_name to list conversations across the pod, pass POD_DEFAULT "
        "(or pod_default) to list default pod assistant conversations, or "
        "pass a name to list conversations for a specific pod agent. Child "
        "(sub-agent) conversations are omitted by default; pass parent_id to "
        "list the children of a specific conversation instead."
    ),
)
async def list_conversations(
    pod_id: UUID,
    request: Request,
    user: CurrentUser,
    service: ConversationServiceDep,
    agent_name: str | None = Query(default=None, min_length=1),
    run_status: ConversationStatus | None = Query(default=None, alias="status"),
    conversation_type: ConversationType | None = Query(default=None, alias="type"),
    parent_id: UUID | None = Query(default=None),
    page_token: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> ConversationListResponse:
    conversations, next_cursor = await service.queries.list_conversations(
        pod_id=pod_id,
        agent_selection=_parse_conversation_agent_selection(agent_name),
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
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Get Pod Conversation",
    description="Get a single pod-scoped assistant or agent conversation by id.",
)
async def get_conversation(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
) -> ConversationResponse:
    conversation = await service.queries.get_conversation(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
    )
    return ConversationResponse.model_validate(conversation)


@router.patch(
    "/{conversation_id}",
    response_model=ConversationResponse,
    operation_id="agent.conversation.update",
    dependencies=[CONVERSATION_MEMBERSHIP],
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
) -> ConversationResponse:
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
    dependencies=[CONVERSATION_MEMBERSHIP],
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
    page_token: str | None = Query(default=None),
    before_sequence: int | None = Query(default=None, ge=0),
    after_sequence: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> MessageListResponse:
    token_sequence = _parse_message_page_token(page_token)
    messages, next_cursor = await service.queries.list_messages(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
        before_sequence=token_sequence
        if token_sequence is not None
        else before_sequence,
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
    dependencies=[CONVERSATION_MEMBERSHIP],
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
    approvals = await service.queries.list_user_approvals(
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
    dependencies=[CONVERSATION_MEMBERSHIP],
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
    # Idempotent + self-healing: resolving an already-recorded approval reconciles
    # its half-finished resume instead of erroring (status "reconciled"). A truly
    # unknown approval raises UnknownApprovalError -> 404 via the domain handler.
    #
    # defer_reconciliation keeps the browser's timeout out of the equation: the
    # decision commits here, and an approved tool (which may legitimately run for
    # minutes) is handed to a worker job, answering with status "queued".
    resolution = await service.resolve_user_approval(
        conversation_id=conversation_id,
        approval_id=approval_id,
        user_id=user.id,
        pod_id=pod_id,
        decision=data.decision,
        response=data.response,
        defer_reconciliation=True,
    )
    return ApprovalDecisionResponse(
        approval_id=approval_id,
        decision=resolution.decision,
        status=resolution.status,
    )


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
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> StreamingResponse:
    # Build the authorization context inside the SHORT uow below rather than via a
    # request-scoped PodContextDep: a StreamingResponse keeps request-scoped
    # dependencies (and their pooled connection) alive for the whole SSE stream,
    # which pins one DB connection per in-flight stream. Here the connection is
    # released the moment add_user_message_and_start_run commits, before streaming.
    async def start_run() -> AgentRunStartResult:
        async with pod_context_scope(
            uow_factory, request=request, user_id=user.id, pod_id=pod_id
        ) as scope:
            assert_pod_membership(scope.ctx, "use conversations in this pod")
            service = _build_conversation_service(scope.uow)
            return await service.add_user_message_and_start_run(
                conversation_id=conversation_id,
                user_id=user.id,
                content=data.content,
                pod_id=pod_id,
                message_metadata=data.metadata,
            )

    return await start_and_stream_run(
        channel_service=channel_service,
        conversation_id=conversation_id,
        start_run=start_run,
    )


@router.post(
    "/{conversation_id}/retry",
    response_model=AgentRunStartResponse,
    operation_id="agent.conversation.retry",
    summary="Retry Failed Pod Conversation Run",
    description=(
        "Start a new run from the latest failed run's persisted conversation "
        "history without appending a duplicate user message. Retry is allowed "
        "only when the failed run produced no assistant, tool, or system activity. "
        "Attach to the returned run with the conversation stream endpoint."
    ),
    responses={
        404: {"description": "Conversation was not found or is not visible"},
        409: {"description": "The latest run is not safely retryable"},
        429: {"description": "The account usage limit was exceeded"},
    },
)
async def retry_failed_run(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> AgentRunStartResponse:
    async with pod_context_scope(
        uow_factory, request=request, user_id=user.id, pod_id=pod_id
    ) as scope:
        assert_pod_membership(scope.ctx, "use conversations in this pod")
        service = _build_conversation_retry_service(scope.uow)
        result = await service.retry_failed_run(
            conversation_id=conversation_id,
            user_id=user.id,
            pod_id=pod_id,
        )
    return AgentRunStartResponse.model_validate(result)


@router.get(
    "/{conversation_id}/stream",
    response_class=StreamingResponse,
    operation_id="agent.conversation.stream",
    summary="Stream Pod Conversation",
    description=(
        "Subscribe to Server-Sent Events for an existing pod-scoped "
        "conversation. The stream closes immediately when the conversation "
        "has no active run. Optionally filter to a specific internal run id "
        "for reconnects; terminal runs replay their persisted terminal event."
    ),
)
async def stream_conversation(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    channel_service: ChannelServiceDep,
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    agent_run_id: UUID | None = Query(default=None),
) -> StreamingResponse:
    # Build the auth context inside short uows (released before/within the stream)
    # rather than via a request-scoped PodContextDep, which would pin a pooled
    # connection for the whole StreamingResponse. See send_message for rationale.
    try:
        async with pod_context_scope(
            uow_factory, request=request, user_id=user.id, pod_id=pod_id
        ) as scope:
            assert_pod_membership(scope.ctx, "use conversations in this pod")
            service = _build_conversation_service(scope.uow)
            await service.queries.get_conversation(
                conversation_id=conversation_id,
                user_id=user.id,
                pod_id=pod_id,
            )
            if agent_run_id is not None:
                await load_authorized_agent_run(
                    service,
                    conversation_id=conversation_id,
                    agent_run_id=agent_run_id,
                    user_id=user.id,
                    pod_id=pod_id,
                )
    except (AgentNotFoundError, ConversationNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc

    async def event_generator() -> AsyncGenerator[str, None]:
        async with channel_service.subscribe(
            [conversation_channel(conversation_id)]
        ) as iterator:
            async with pod_context_scope(
                uow_factory, request=request, user_id=user.id, pod_id=pod_id
            ) as scope:
                service = _build_conversation_service(scope.uow)
                target_run = (
                    await load_authorized_agent_run(
                        service,
                        conversation_id=conversation_id,
                        agent_run_id=agent_run_id,
                        user_id=user.id,
                        pod_id=pod_id,
                    )
                    if agent_run_id is not None
                    else await service.queries.get_active_agent_run(
                        conversation_id=conversation_id,
                        user_id=user.id,
                        pod_id=pod_id,
                    )
                )
            if target_run is None:
                return

            terminal_chunk = terminal_run_chunk(target_run)
            if terminal_chunk is not None:
                yield terminal_chunk
                return

            async for chunk in iter_subscription(iterator, target_run.id):
                yield chunk

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post(
    "/{conversation_id}/stop",
    response_model=ConversationResponse,
    operation_id="agent.conversation.stop",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Stop Pod Conversation",
    description="Request cancellation of the active internal run for a conversation.",
)
async def stop_conversation(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
) -> ConversationResponse:
    conversation = await service.stop_conversation(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
    )
    return ConversationResponse.model_validate(conversation)
