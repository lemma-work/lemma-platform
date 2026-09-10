"""Private browser email-code login using the shared canonical identity workflow."""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.api.dependencies import get_uow_factory
from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.identity.contracts.onboarding import email_challenge_service
from app.modules.identity.domain.email_challenge import PENDING_TTL_SECONDS
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    create_browser_session,
)
from app.modules.identity.services.auth_abuse import RateLimitExceeded, client_ip
from app.modules.identity.services.email_challenges import (
    ChallengeRejected,
    EmailChallengeService,
)
from app.modules.identity.services.verified_accounts import complete_verified_account

router = APIRouter(prefix="/auth/email-code", tags=["Auth"], include_in_schema=False)
_COOKIE = "lemma_email_login_nonce"


def get_email_login_challenges() -> EmailChallengeService:
    return email_challenge_service("web")


class StartEmailLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    nonce: str = Field(min_length=43, max_length=43)


class EmailLoginChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID
    nonce: str = Field(min_length=43, max_length=43)


class VerifyEmailLogin(EmailLoginChallenge):
    code: str = Field(pattern=r"^[0-9]{6}$")


class EmailLoginReceipt(BaseModel):
    challenge_id: UUID
    expires_at: datetime


def _require_origin(request: Request) -> None:
    allowed = {
        f"{parsed.scheme}://{parsed.netloc}"
        for value in (settings.auth_frontend_url, settings.frontend_url)
        if (parsed := urlsplit(value)).scheme and parsed.netloc
    }
    if request.headers.get("origin") not in allowed:
        raise HTTPException(
            status_code=403, detail="Open email login from Lemma's auth page"
        )
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(status_code=415, detail="JSON is required")


def _binding(request: Request, nonce: str) -> str:
    _require_origin(request)
    cookie = request.cookies.get(_COOKIE, "")
    if not cookie or not hmac.compare_digest(cookie, nonce):
        raise HTTPException(
            status_code=403, detail="Login expired; start again in this browser"
        )
    return cookie


def _challenge_error(error: ChallengeRejected | RateLimitExceeded) -> HTTPException:
    if isinstance(error, RateLimitExceeded):
        return HTTPException(
            status_code=429,
            detail="Too many code requests; try again later",
            headers={"Retry-After": str(error.retry_after_seconds)},
        )
    return HTTPException(status_code=400, detail=error.message)


@router.post("/browser")
async def initialize_email_login(
    request: Request, response: Response
) -> dict[str, str]:
    _require_origin(request)
    nonce = secrets.token_urlsafe(32)
    response.set_cookie(
        _COOKIE,
        nonce,
        max_age=PENDING_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/auth/email-code",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"nonce": nonce}


@router.post("/start", response_model=EmailLoginReceipt)
async def start_email_login(
    data: StartEmailLogin,
    request: Request,
    challenges: EmailChallengeService = Depends(get_email_login_challenges),
) -> EmailLoginReceipt:
    binding = _binding(request, data.nonce)
    try:
        receipt = await challenges.start(
            email=str(data.email),
            binding=binding,
            purpose="browser_login",
            sender_key=f"browser:{client_ip(request.scope)}",
        )
    except (ChallengeRejected, RateLimitExceeded) as error:
        raise _challenge_error(error) from error
    return EmailLoginReceipt(challenge_id=receipt.id, expires_at=receipt.expires_at)


@router.post("/resend", response_model=EmailLoginReceipt)
async def resend_email_login(
    data: EmailLoginChallenge,
    request: Request,
    challenges: EmailChallengeService = Depends(get_email_login_challenges),
) -> EmailLoginReceipt:
    binding = _binding(request, data.nonce)
    try:
        receipt = await challenges.resend(
            challenge_id=data.challenge_id,
            binding=binding,
            purpose="browser_login",
            sender_key=f"browser:{client_ip(request.scope)}",
        )
    except (ChallengeRejected, RateLimitExceeded) as error:
        raise _challenge_error(error) from error
    return EmailLoginReceipt(challenge_id=receipt.id, expires_at=receipt.expires_at)


@router.post("/verify")
async def verify_email_login(
    data: VerifyEmailLogin,
    request: Request,
    uows: UnitOfWorkFactory = Depends(get_uow_factory),
    challenges: EmailChallengeService = Depends(get_email_login_challenges),
) -> dict[str, str]:
    binding = _binding(request, data.nonce)
    try:
        await challenges.verify(
            challenge_id=data.challenge_id,
            binding=binding,
            purpose="browser_login",
            submitted_code=data.code,
        )
        user_id = await complete_verified_account(
            uows,
            operation_id=data.challenge_id,
            binding=binding,
            purpose="browser_login",
        )
    except ChallengeRejected as error:
        raise _challenge_error(error) from error
    await create_browser_session(request, user_id, client="email-code")
    return {"status": "complete"}
