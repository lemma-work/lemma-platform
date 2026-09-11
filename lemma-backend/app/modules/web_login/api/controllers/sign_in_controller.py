"""The routes a person uses while signing a site in for an agent.

What this is not: a place where an agent's link grants anything. The request id
is a lookup, not a credential — every route resolves it against the caller's own
session, so a link forwarded to somebody else answers exactly as an invented id
does. That is what makes the link safe to send over WhatsApp, where unfurlers
will fetch it before any person does.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.modules.web_login.domain.entities import SignInRequestStatus
from app.modules.web_login.infrastructure.sign_in_repository import (
    SignInRequestNotFound,
    SignInRequestRepository,
)
from app.modules.web_login.services.sign_in import NotSignedInYet, SignInService

router = APIRouter(prefix="/web-logins/sign-in-requests", tags=["Web Logins"])


class SignInRequestResponse(BaseModel):
    id: UUID
    origin: str
    reason: str
    status: SignInRequestStatus
    created_at: datetime
    saved: bool = Field(
        default=False,
        description="Whether the login was kept for next time.",
    )
    saved_detail: str | None = Field(
        default=None,
        description="Why it was not kept, in words, when it was not.",
    )


class FinishSignInRequest(BaseModel):
    force: bool = Field(
        default=False,
        description=(
            "Save whatever the browser holds even though it does not look "
            "signed in. For sites the check reads wrongly."
        ),
    )


def _view(request) -> SignInRequestResponse:
    return SignInRequestResponse(
        id=request.id,
        origin=request.origin,
        reason=request.reason,
        status=request.status,
        created_at=request.created_at,
        saved=request.saved,
        saved_detail=request.saved_detail,
    )


@router.get(
    "/{request_id}",
    response_model=SignInRequestResponse,
    operation_id="web_login.sign_in_request.get",
    summary="What a sign-in request is asking for",
)
async def get_sign_in_request(
    request_id: UUID, user: CurrentUser, uow: UoWDep
) -> SignInRequestResponse:
    found = await SignInRequestRepository(uow.session).get_for_user(request_id, user.id)
    if found is None:
        # Deliberately the same answer as an id that never existed: "that one
        # exists but is not yours" tells somebody holding a guessed id that
        # they guessed right.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such sign-in request"
        )
    return _view(found)


@router.post(
    "/{request_id}:finish",
    response_model=SignInRequestResponse,
    operation_id="web_login.sign_in_request.finish",
    summary="Say you have signed in",
)
async def finish_sign_in_request(
    request_id: UUID,
    body: FinishSignInRequest,
    user: CurrentUser,
) -> SignInRequestResponse:
    """Capture what the browser now holds, and let the waiting run carry on.

    The capture happens here, while the person is still present, rather than
    later in the resumed run — so that "it did not work" is something they can
    be told at the moment they can still fix it.
    """
    service = SignInService(get_uow_factory())
    try:
        finished = await service.finish(
            request_id=request_id, user_id=user.id, force=body.force
        )
    except SignInRequestNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such sign-in request"
        )
    except NotSignedInYet:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "It does not look like you are signed in yet — the browser "
                "holds nothing for this site. Finish signing in, then try again."
            ),
        )
    finally:
        await service.close()
    return _view(finished)


@router.post(
    "/{request_id}:decline",
    response_model=SignInRequestResponse,
    operation_id="web_login.sign_in_request.decline",
    summary="Say you cannot sign in right now",
)
async def decline_sign_in_request(
    request_id: UUID, user: CurrentUser
) -> SignInRequestResponse:
    service = SignInService(get_uow_factory())
    try:
        declined = await service.decline(request_id=request_id, user_id=user.id)
    except SignInRequestNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such sign-in request"
        )
    finally:
        await service.close()
    return _view(declined)


__all__ = ["router"]
