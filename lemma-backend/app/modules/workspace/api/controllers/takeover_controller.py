"""Opening, keeping alive, and closing a browser takeover.

The person is driving the agent's browser through the signed dashboard URL the
port proxy serves. Three things have to be true for that to work, and each is a
route here.

**It has to be theirs.** The request id travels through Slack and WhatsApp,
whose unfurl bots fetch every link they are shown, so the id alone grants
nothing: every read is checked against the caller's own session.

**The browser has to still be there when they arrive.** ``agent-browser`` closes
Chrome after two minutes without a command and the sandbox releases after
fifteen minutes idle — both shorter than finding a password and getting through
a second factor. The heartbeat is not a nicety; without it the page a person is
typing into disappears under them.

**The agent has to learn how it ended.** Resolving the request is what unblocks
whatever asked for it — and, when it went well, what saves the session so nobody
is asked for this site again. Capturing on resolve rather than in a second call
is deliberate: two round trips is two chances to do one without the other, and
the one that gets skipped is always the one that makes it worth doing.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Annotated

from app.core.api.dependencies import CurrentUser
from app.core.log.log import get_logger
from app.modules.workspace.api.controllers.browser_controller import touch_browser
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.session_support import sandbox_failure_types
from app.modules.workspace.services.takeover import (
    TakeoverNotFound,
    TakeoverRequest,
    TakeoverStatus,
    TakeoverStore,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/workspace/takeover", tags=["Workspace"])

# Long enough to cover a slow page and a second factor, short enough that a
# forgotten tab does not hold a browser open indefinitely. The client re-mints
# while the person is still there.
_ACCESS_TTL_SECONDS = 600


def get_takeover_store() -> TakeoverStore:
    return TakeoverStore()


def get_workspace_service() -> WorkspaceSandboxService:
    return WorkspaceSandboxService()


TakeoverStoreDep = Annotated[TakeoverStore, Depends(get_takeover_store)]
WorkspaceServiceDep = Annotated[WorkspaceSandboxService, Depends(get_workspace_service)]


class TakeoverCreateRequest(BaseModel):
    origin: str = Field(
        max_length=255,
        description="The site being logged into, e.g. `https://app.example.com`.",
    )
    conversation_id: str | None = Field(
        default=None, description="The conversation that raised this."
    )
    reason: str = Field(
        default="",
        max_length=500,
        description="What the agent was trying to do when it hit the wall.",
    )


class TakeoverResponse(BaseModel):
    request_id: str
    origin: str
    reason: str
    status: TakeoverStatus
    conversation_id: str | None
    created_at: datetime
    saved: bool = Field(
        default=False,
        description="Whether the session was kept, so this site is not asked about again.",
    )
    saved_detail: str = Field(
        default="",
        description="Why it was or was not kept, in words for the person who just signed in.",
    )


class TakeoverSessionResponse(TakeoverResponse):
    url: str = Field(description="Signed URL of the live browser view.")
    expires_at: datetime = Field(description="When that URL stops working.")


def _view(request: TakeoverRequest) -> dict[str, object]:
    return {
        "request_id": request.request_id,
        "origin": request.origin,
        "reason": request.reason,
        "status": request.status,
        "conversation_id": (
            str(request.conversation_id) if request.conversation_id else None
        ),
        "created_at": request.created_at,
    }


@router.post(
    "",
    response_model=TakeoverResponse,
    operation_id="workspace.takeover.create",
    summary="Ask a person to drive the workspace browser",
)
async def create_takeover(
    body: TakeoverCreateRequest,
    user: CurrentUser,
    store: TakeoverStoreDep,
) -> TakeoverResponse:
    from uuid import UUID

    request = await store.create(
        user_id=user.id,
        conversation_id=UUID(body.conversation_id) if body.conversation_id else None,
        origin=body.origin,
        reason=body.reason,
    )
    return TakeoverResponse(**_view(request))


@router.get(
    "/{request_id}",
    response_model=TakeoverSessionResponse,
    operation_id="workspace.takeover.open",
    summary="Open a takeover and get the live browser URL",
)
async def open_takeover(
    request_id: str,
    user: CurrentUser,
    store: TakeoverStoreDep,
    service: WorkspaceServiceDep,
) -> TakeoverSessionResponse:
    if not workspace_settings.runtime_credential_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace port access signing key is not configured",
        )
    try:
        request = await store.get_for_user(request_id, user.id)
    except TakeoverNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That request has expired or does not exist.",
        )

    try:
        access = await service.create_browser_access(
            user.id, ttl_seconds=_ACCESS_TTL_SECONDS
        )
    finally:
        await service.close()

    return TakeoverSessionResponse(
        **_view(request), url=access.url, expires_at=access.expires_at
    )


@router.post(
    "/{request_id}:heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workspace.takeover.heartbeat",
    summary="Keep the browser alive while somebody is typing",
)
async def heartbeat_takeover(
    request_id: str,
    user: CurrentUser,
    store: TakeoverStoreDep,
    service: WorkspaceServiceDep,
) -> None:
    """Touch the browser so neither timer closes it under the person.

    A trivial `agent-browser` command is what resets the daemon's two-minute
    idle timer, and running it through the workspace session is what refreshes
    the sandbox's own activity clock. One call answers both.
    """
    try:
        await store.get_for_user(request_id, user.id)
    except TakeoverNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That request has expired or does not exist.",
        )

    try:
        await touch_browser(service, user.id)
    finally:
        await service.close()


@router.post(
    "/{request_id}:resolve",
    response_model=TakeoverResponse,
    operation_id="workspace.takeover.resolve",
    summary="Say the takeover is finished",
)
async def resolve_takeover(
    request_id: str,
    user: CurrentUser,
    store: TakeoverStoreDep,
    service: WorkspaceServiceDep,
    done: bool = True,
) -> TakeoverResponse:
    try:
        request = await store.resolve(
            request_id,
            user.id,
            status=TakeoverStatus.DONE if done else TakeoverStatus.CANCELLED,
        )
    except TakeoverNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That request has expired or does not exist.",
        )

    if not done:
        return TakeoverResponse(**_view(request))

    outcome = await _keep_the_session(user.id, request, service)
    return TakeoverResponse(
        **_view(request), saved=outcome.saved, saved_detail=outcome.reason
    )


async def _keep_the_session(user_id, request, service):
    """Save what the person just signed in to, so the next run does not ask.

    Failing here never fails the takeover: they *are* signed in, and what was
    lost is only the remembering. The response says which it was rather than
    implying success.
    """
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.web_login.contracts import (
        CaptureOutcome,
        WebLoginRepository,
        capture_session,
    )

    try:
        session = await service.get_session(
            user_id, pod_id=None, initial_cwd="/workspace", close_on_exit=False
        )
        async with session:
            async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
                outcome = await capture_session(
                    session,
                    WebLoginRepository(uow.session),
                    user_id=user_id,
                    origin=request.origin,
                    label=request.origin,
                    conversation_id=request.conversation_id,
                )
                await uow.commit()
        return outcome
    except sandbox_failure_types():
        logger.warning("workspace.takeover.capture_failed.degraded", exc_info=True)
        return CaptureOutcome(
            False,
            "You are signed in, but the session could not be saved — you may be "
            "asked again for this site.",
        )
    finally:
        await service.close()
