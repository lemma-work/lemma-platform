"""The routes a person uses while signing a site in for an agent.

Addressed by the pause itself -- the conversation and the tool call that is
waiting -- rather than by a row of this feature's own. There used to be a
`web_login_sign_in_requests` table holding the origin, the reason and a status;
all three already existed on the paused tool call, and the two copies drifted.
The status was the worst of it: a stale tab could move the row while the
agent's decision said the opposite, and the two disagreed permanently.

What this is not: a place where an agent's link grants anything. The ids in the
URL are a lookup, not a credential -- both routes resolve them against the
caller's own session, so a link forwarded to somebody else answers exactly as an
invented one does. That is what makes the link safe to send over WhatsApp, where
unfurlers fetch it before any person does.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.modules.web_login.services.sign_in import (
    SignInNotPending,
    SignInService,
)

router = APIRouter(prefix="/web-logins/sign-ins", tags=["Web Logins"])


class PendingSignInResponse(BaseModel):
    """What the page needs to put a site in front of somebody."""

    tool_call_id: str
    origin: str
    reason: str


class AnswerSignInRequest(BaseModel):
    signed_in: bool = Field(
        description=(
            "True when the person says they have signed in; false when they "
            "cannot right now."
        )
    )


class SignInOutcomeResponse(BaseModel):
    origin: str
    signed_in: bool
    working: bool = Field(
        default=False,
        description=(
            "Whether the site stopped asking for a login straight afterwards. "
            "Reported, not enforced: the person has already done what was "
            "asked, and a site that shows a form at the same address under a "
            "neutral title reads as still asking."
        ),
    )


@router.get(
    "/{conversation_id}/{tool_call_id}",
    response_model=PendingSignInResponse,
    operation_id="web_login.sign_in.pending",
    summary="What a sign-in link is asking for",
)
async def get_pending_sign_in(
    conversation_id: UUID,
    tool_call_id: str,
    user: CurrentUser,
) -> PendingSignInResponse:
    service = SignInService(get_uow_factory())
    try:
        found = await service.pending(conversation_id=conversation_id, user_id=user.id)
    finally:
        await service.close()
    if found is None or found.tool_call_id != tool_call_id:
        # One answer for "never asked", "already answered" and "not yours".
        # They are the same fact from the page's side -- this link is spent --
        # and distinguishing them would tell a stranger which is which.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sign-in is waiting on this link",
        )
    return PendingSignInResponse(
        tool_call_id=found.tool_call_id,
        origin=found.origin,
        reason=found.reason,
    )


@router.post(
    "/{conversation_id}/{tool_call_id}/answer",
    response_model=SignInOutcomeResponse,
    operation_id="web_login.sign_in.answer",
    summary="Say whether you signed in",
)
async def answer_sign_in(
    conversation_id: UUID,
    tool_call_id: str,
    body: AnswerSignInRequest,
    user: CurrentUser,
) -> SignInOutcomeResponse:
    """Let the waiting run carry on, and check the site while they are here.

    Nothing is captured: the browser keeps its own profile, so finishing a
    sign-in is the person finishing it. What this does do is look at the site
    straight afterwards, while they are still present -- so "it still wants a
    login" is something they hear now rather than the agent discovering it.

    One route for both answers because it is one answer. Two routes meant two
    status writes with two different guards, and the weaker one let a stale tab
    overwrite a decision the agent had already been given.
    """
    service = SignInService(get_uow_factory())
    try:
        outcome = await service.answer(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            user_id=user.id,
            signed_in=body.signed_in,
        )
    except SignInNotPending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sign-in is waiting on this link",
        )
    finally:
        await service.close()
    return SignInOutcomeResponse(
        origin=outcome.origin,
        signed_in=outcome.signed_in,
        working=outcome.working,
    )
