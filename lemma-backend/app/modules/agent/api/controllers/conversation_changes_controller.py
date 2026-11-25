"""What a repository conversation has changed in its checkout, for a Changes card.

Split from ``conversation_controller``, which is at the size ratchet. Not
behind ``CONVERSATION_MEMBERSHIP``: that resolves the pod context in a
request-scoped unit of work, whose connection would be held while the machine
is asked. ``member_unit_of_work`` checks membership in a short one instead,
closed before the machine is reached (``application/conversations/changes``).
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.api.changes_schemas import ConversationChangesResponse
from app.modules.agent.api.controllers.shared import member_unit_of_work
from app.modules.agent.application.conversations.changes import (
    ConversationChangesReader,
)
from app.modules.agent.domain.conversation_changes import (
    DEFAULT_PATCH_BYTES,
    MAX_PATCH_BYTES,
)

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)


@router.get(
    "/{conversation_id}/changes",
    response_model=ConversationChangesResponse,
    operation_id="agent.conversation.changes.get",
    summary="Get Conversation Changes",
    description=(
        "What a conversation opened in a repository has changed in its "
        "checkout, live: every file that differs from where its branch left "
        "the start point (`origin/<ref>`, else `origin/HEAD`) -- commits not "
        "yet pushed, staged and unstaged edits -- and its untracked files, "
        "with line counts, and the patch with `patch=true`. Read-only: "
        "nothing is fetched and the repository is not written. A machine that "
        "is not running is not started (`machine_unavailable`); a conversation "
        "whose last run worked on its person's Mac answers `elsewhere`. A read "
        "that runs past its ceiling is a 504, `CONVERSATION_CHANGES_TIMED_OUT`. "
        "Only the conversation's owner may read it."
    ),
)
async def get_conversation_changes(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    request: Request,
    patch: bool = Query(default=False, description="Include the unified diff."),
    max_patch_bytes: int = Query(
        default=DEFAULT_PATCH_BYTES,
        ge=1024,
        le=MAX_PATCH_BYTES,
        description="Cut the patch at this many bytes; `truncated` says so.",
    ),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> ConversationChangesResponse:
    reader = ConversationChangesReader.build(
        partial(
            member_unit_of_work,
            uow_factory,
            request=request,
            user_id=user.id,
            pod_id=pod_id,
        )
    )
    changes = await reader.read(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
        patch_bytes=max_patch_bytes if patch else None,
    )
    return ConversationChangesResponse.of(changes)
