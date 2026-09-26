"""Private browser email-code login using the shared canonical identity workflow."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select

from app.core.api.dependencies import get_uow_factory
from app.core.config import settings
from app.core.email.email_sender import email_delivery_state
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.identity.contracts.onboarding import email_challenge_service
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.email_challenge import PENDING_TTL_SECONDS
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    get_conflicting_thirdparty_id,
    has_emailpassword_login_method,
    has_passwordless_login_method,
    list_users_by_email,
)
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    create_browser_session,
)
from app.modules.identity.services.auth_abuse import (
    AltchaRejected,
    RateLimitExceeded,
    client_ip,
    get_auth_abuse_store,
)
from app.modules.identity.services.email_challenges import (
    ChallengeRejected,
    EmailChallengeService,
)
from app.modules.identity.services.verified_accounts import complete_verified_account

logger = get_logger(__name__)

EMAIL_CODE_NOT_CONFIGURED_MESSAGE = (
    "Email isn't set up on this Lemma, so a sign-in code can't be sent. "
    "Use a password instead."
)

router = APIRouter(prefix="/auth/email-code", tags=["Auth"], include_in_schema=False)
_COOKIE = "lemma_email_login_nonce"


def get_email_login_challenges() -> EmailChallengeService:
    return email_challenge_service("web")


#: How `/continue` is metered. A named dependency for the same reason
#: `EmailChallengeService` takes `enforce_send_limits` as a collaborator: every
#: test in this suite shares one client IP, so a ceiling wired in directly would
#: be a budget spent by whichever tests happened to run first. One test exercises
#: the real thing; the rest stand something inert in front of it.
MethodLookupLimits = Callable[[Request, str], Awaitable[None]]


def get_method_lookup_limits() -> MethodLookupLimits:
    return meter_method_lookup


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


class ContinueEmailLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    nonce: str = Field(min_length=43, max_length=43)
    abandon_challenge_id: UUID | None = Field(
        default=None,
        description=(
            "A challenge this browser is walking away from. The sixty-second "
            "cooldown in `start_challenge` is keyed on the binding rather than "
            "the address, so without retiring the old one first, correcting a "
            "typo inside a minute is refused."
        ),
    )


class PasswordMethod(BaseModel):
    """Ask them for a password. Also the answer for an account that cannot sign in.

    Deliberately the same reply in both cases. `sign_in_post` already refuses a
    suspended account in words indistinguishable from a wrong password, so
    sending them to the password field costs nothing, discloses nothing, and --
    unlike routing them to a code -- puts no mail in a suspended mailbox.
    """

    method: Literal["password"] = "password"


class ThirdPartyMethod(BaseModel):
    method: Literal["thirdparty"] = "thirdparty"
    provider: str


class CodeMethod(BaseModel):
    """A code is already on its way. Sent for a passwordless account *and* for an
    address with no account at all, which is what keeps the two indistinguishable:
    `complete_verified_account` creates the account when there is none, so signing
    up and signing in are the same act and need no separate answer here.
    """

    method: Literal["code"] = "code"
    challenge_id: UUID
    expires_at: datetime


ContinueResponse = Annotated[
    PasswordMethod | ThirdPartyMethod | CodeMethod,
    Field(discriminator="method"),
]


def _require_origin(request: Request) -> None:
    def canonical_origin(value: str) -> str | None:
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return None
            port = parsed.port
        except ValueError:
            return None
        scheme = parsed.scheme.lower()
        if port == (443 if scheme == "https" else 80 if scheme == "http" else None):
            port = None
        hostname = parsed.hostname.lower()
        if ":" in hostname:
            hostname = f"[{hostname}]"
        authority = hostname if port is None else f"{hostname}:{port}"
        return f"{scheme}://{authority}"

    allowed = {
        origin
        for value in (settings.auth_frontend_url, settings.frontend_url)
        if (origin := canonical_origin(value)) is not None
    }
    if canonical_origin(request.headers.get("origin", "")) not in allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "EMAIL_LOGIN_ORIGIN_NOT_ALLOWED",
                "message": "This email sign-in page is not configured for this service. Open Lemma's auth page and try again.",
            },
        )
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(
            status_code=415,
            detail={
                "code": "EMAIL_LOGIN_CONTENT_TYPE_REQUIRED",
                "message": "Email sign-in requires a JSON request.",
            },
        )


def _binding(request: Request, nonce: str) -> str:
    _require_origin(request)
    cookie = request.cookies.get(_COOKIE, "")
    if not cookie or not hmac.compare_digest(cookie, nonce):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "EMAIL_LOGIN_EXPIRED",
                "message": "Login expired; start again in this browser.",
            },
        )
    return cookie


def _challenge_error(error: ChallengeRejected | RateLimitExceeded) -> HTTPException:
    if isinstance(error, RateLimitExceeded):
        return HTTPException(
            status_code=429,
            detail={
                "code": "EMAIL_CODE_RATE_LIMITED",
                "message": "Too many code requests; try again later",
            },
            headers={"Retry-After": str(error.retry_after_seconds)},
        )
    return HTTPException(
        status_code=400,
        detail={"code": error.code, "message": error.message},
    )


async def meter_method_lookup(request: Request, email: str) -> None:
    """Bound the one question this endpoint answers: which door does this address use.

    That answer is already free today. `sign_in_post` compares login methods
    *before* it checks the password, and the refusals it returns for a
    passwordless or third-party address are `SIGN_IN_NOT_ALLOWED` -- which
    `_record_signin_result` does not count, because it only increments on
    `WRONG_CREDENTIALS_ERROR`. So the probe exists, and nothing but the global
    per-IP ceiling stands in front of it. This is the first real budget for it.

    Escalating to a proof rather than demanding one every time is deliberate.
    The emailpassword `preAPIHook` already attaches a `signin-risk` proof to
    every `/auth/signin`, so gating this unconditionally would make an ordinary
    password sign-in solve proof-of-work twice -- once to find out a password is
    wanted, once to send it.
    """
    store = get_auth_abuse_store()
    ip_hash = store.digest(client_ip(request.scope))
    email_hash = store.digest(email)
    burst_key = f"identity:rate:email-methods:ip:15m:{ip_hash}"
    # Read before enforcing: `enforce` increments, so asking afterwards would
    # count this request against the threshold that decides whether to ask for
    # a proof, and the first caller past the line would be the one before it.
    seen = await store.count(burst_key)
    await store.enforce(burst_key, limit=20, window_seconds=900)
    await store.enforce(
        f"identity:rate:email-methods:ip:day:{ip_hash}",
        limit=100,
        window_seconds=86_400,
    )
    await store.enforce(
        f"identity:rate:email-methods:email:15m:{email_hash}",
        limit=10,
        window_seconds=900,
    )
    if seen >= 10:
        await store.verify_altcha(
            request.headers.get("x-altcha-payload"), purpose="signin-risk"
        )


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


async def _method_for(
    uows: UnitOfWorkFactory,
    challenges: EmailChallengeService,
    *,
    email: str,
    binding: str,
    sender_key: str,
    abandon: UUID | None,
) -> PasswordMethod | ThirdPartyMethod | CodeMethod:
    """Which door this address uses -- and, when it is a code, open it on the way."""
    async with uows() as uow:
        local = await uow.session.scalar(
            select(User).where(func.lower(User.email) == email)
        )
        cannot_sign_in = local is not None and (not local.is_active or local.is_deleted)
    if cannot_sign_in:
        return PasswordMethod()

    users = await list_users_by_email(tenant_id="public", email=email, user_context={})
    # Passwordless is asked first because it is the method that wins everywhere
    # else: `sign_in_post` refuses a password for these accounts and
    # `override_thirdparty` refuses Google, so a code is the only door that
    # actually opens. Asking in any other order would offer a field or a button
    # that is guaranteed to be turned away.
    if not has_passwordless_login_method(users, email):
        if has_emailpassword_login_method(users, email):
            return PasswordMethod()
        provider = get_conflicting_thirdparty_id(users, email=email)
        if provider is not None:
            return ThirdPartyMethod(provider=provider)

    if abandon is not None:
        try:
            await challenges.cancel_challenge(
                challenge_id=abandon, binding=binding, purpose="browser_login"
            )
        except ChallengeRejected:
            # An id that is stale, already spent, or not this browser's is
            # nothing to retire, and saying so helps nobody: the person is
            # correcting a typo. If a live challenge really is still in the way,
            # `start_challenge` refuses next and says it in words they can act on.
            logger.info("identity.email_login.abandon_ignored")
    # Before a challenge is minted, so nobody is left waiting for a code that
    # has nowhere to go -- a Lemma Desktop nobody has set email up on. The
    # answer is the same for every address that reaches here, so it says
    # nothing about accounts.
    if email_delivery_state() == "not_configured":
        raise ChallengeRejected(
            EMAIL_CODE_NOT_CONFIGURED_MESSAGE, code="EMAIL_NOT_CONFIGURED"
        )
    receipt = await challenges.start_challenge(
        email=email,
        binding=binding,
        purpose="browser_login",
        sender_key=sender_key,
    )
    return CodeMethod(challenge_id=receipt.id, expires_at=receipt.expires_at)


@router.post("/continue", response_model=ContinueResponse)
async def continue_email_login(
    data: ContinueEmailLogin,
    request: Request,
    uows: UnitOfWorkFactory = Depends(get_uow_factory),
    challenges: EmailChallengeService = Depends(get_email_login_challenges),
    limits: MethodLookupLimits = Depends(get_method_lookup_limits),
) -> PasswordMethod | ThirdPartyMethod | CodeMethod:
    """One email box, one answer: a password field, a provider button, or a code.

    The branch and the action are the same call on purpose. A separate "what
    does this address use" lookup would be a second round trip and a naked
    oracle; folding the send into the answer means the common case costs one
    request and no code is ever sent to an address that did not need one.
    """
    binding = _binding(request, data.nonce)
    try:
        email = normalize_identity_email(str(data.email))
    except ValueError as error:
        # About the address itself, not about any account behind it -- so this
        # is a syntax answer and discloses nothing.
        raise HTTPException(
            status_code=400,
            detail={
                "code": "EMAIL_LOGIN_INVALID_EMAIL",
                "message": "Enter a valid email address",
            },
        ) from error
    try:
        await limits(request, email)
    except RateLimitExceeded as error:
        raise _challenge_error(error) from error
    except AltchaRejected as error:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "EMAIL_LOGIN_PROOF_REJECTED",
                "message": str(error),
            },
        ) from error

    try:
        answer = await _method_for(
            uows,
            challenges,
            email=email,
            binding=binding,
            sender_key=f"browser:{client_ip(request.scope)}",
            abandon=data.abandon_challenge_id,
        )
    except (ChallengeRejected, RateLimitExceeded) as error:
        raise _challenge_error(error) from error
    logger.info("identity.email_login.continue_resolved", method=answer.method)
    return answer


@router.post("/start", response_model=EmailLoginReceipt)
async def start_email_login(
    data: StartEmailLogin,
    request: Request,
    challenges: EmailChallengeService = Depends(get_email_login_challenges),
) -> EmailLoginReceipt:
    binding = _binding(request, data.nonce)
    try:
        receipt = await challenges.start_challenge(
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
        receipt = await challenges.resend_challenge(
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
        await challenges.verify_challenge(
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
