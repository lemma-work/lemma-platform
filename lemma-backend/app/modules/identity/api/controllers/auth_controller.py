from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from supertokens_python.recipe.session.asyncio import (
    get_session,
    revoke_all_sessions_for_user,
)

from app.core.config import reveal_secret, settings
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.api.dependencies import PodMembershipDep, UserServiceDep
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    create_cli_session_tokens,
    create_browser_session,
    create_desktop_browser_session,
    refresh_cli_session_tokens,
)
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache
from app.modules.identity.services.auth_abuse import (
    RateLimitExceeded,
    client_ip,
    get_auth_abuse_store,
)
from app.modules.identity.services.telegram_oidc import (
    TelegramOIDCError,
    TelegramPurpose,
    get_telegram_oidc_service,
    safe_return_to,
)
from app.modules.identity.services.desktop_auth_handoff import (
    DesktopAuthCompletionConflict,
    DesktopAuthRequestNotFound,
    DesktopAuthRequestPending,
    DesktopAuthRateLimitExceeded,
    DesktopAuthVerifierRejected,
    get_desktop_auth_handoff_store,
)
from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_POD_ID,
    CLAIM_SCOPE,
    WorkloadPrincipalType,
)

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
    redirect_slashes=False,
)


class VerifyTokenResponse(BaseModel):
    user_id: UUID
    email: EmailStr
    pod_id: UUID | None = None
    organization_id: UUID | None = None
    function_id: UUID | None = None
    function_name: str | None = None
    scopes: list[str] = Field(default_factory=list)


class CliAuthInfoResponse(BaseModel):
    api_url: str
    auth_frontend_url: str


class CliSessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    access_token_expires_at: int
    session_handle: str
    user_id: UUID
    email: EmailStr
    token_type: str = "Bearer"


class CliRefreshRequest(BaseModel):
    refresh_token: str


class DesktopAuthRequestCreate(BaseModel):
    code_challenge: str = Field(
        min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )


class DesktopAuthRequestResponse(BaseModel):
    request_id: str
    expires_in_seconds: int


class DesktopAuthCompleteResponse(BaseModel):
    status: str = "complete"


class DesktopAuthSessionRequest(BaseModel):
    request_id: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    code_verifier: str = Field(
        min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )


class DesktopAuthSessionResponse(BaseModel):
    status: str = "complete"
    user_id: UUID
    session_handle: str


class AltchaChallengeResponse(BaseModel):
    enabled: bool
    algorithm: str | None = None
    challenge: str | None = None
    maxnumber: int | None = None
    salt: str | None = None
    signature: str | None = None


class TelegramConfigResponse(BaseModel):
    enabled: bool


class BounceEvent(BaseModel):
    email: EmailStr
    event: Literal["hard_bounce", "soft_bounce"]


def verify_bounce_signature(
    *,
    timestamp: str,
    signature: str,
    body: bytes,
    secret: str,
    now: int | None = None,
) -> None:
    """Validate the provider adapter's timestamped HMAC envelope."""
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid webhook signature"
        ) from exc
    if abs((now if now is not None else int(time.time())) - timestamp_value) > 300:
        raise HTTPException(status_code=401, detail="Webhook timestamp expired")
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    candidate = signature.removeprefix("sha256=")
    if not hmac.compare_digest(expected, candidate):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


async def _enforce_telegram_rate_limit(request: Request) -> None:
    store = get_auth_abuse_store()
    ip_hash = store.digest(client_ip(request.scope))
    try:
        await store.enforce(
            f"identity:rate:telegram:ip:{ip_hash}", limit=10, window_seconds=900
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many Telegram login requests",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc


def _telegram_error_redirect(return_to: str | None, code: str) -> RedirectResponse:
    destination = safe_return_to(return_to)
    separator = "&" if "?" in destination else "?"
    return RedirectResponse(
        f"{destination}{separator}{urlencode({'telegram_error': code})}",
        status_code=303,
    )


@router.get(
    "/altcha/challenge",
    include_in_schema=False,
    response_model=AltchaChallengeResponse,
)
async def create_altcha_challenge(
    purpose: Literal["signup", "verification", "password-reset", "signin-risk"],
) -> AltchaChallengeResponse:
    try:
        challenge = await get_auth_abuse_store().issue_altcha(purpose)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AltchaChallengeResponse.model_validate(challenge)


@router.get(
    "/telegram/config",
    include_in_schema=False,
    response_model=TelegramConfigResponse,
)
async def telegram_config() -> TelegramConfigResponse:
    return TelegramConfigResponse(enabled=settings.is_telegram_oidc_configured())


@router.get("/telegram/start", include_in_schema=False)
async def telegram_start(
    request: Request,
    purpose: TelegramPurpose = Query(default="signin"),
    return_to: str | None = Query(default=None, max_length=2048),
) -> RedirectResponse:
    await _enforce_telegram_rate_limit(request)
    user_id: UUID | None = None
    if purpose == "verify_mobile":
        session = await get_session(request, session_required=True)
        if session is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        user_id = UUID(session.get_user_id())
        async with async_session_maker() as db_session:
            user = await db_session.get(User, user_id)
        if user is None or not user.is_active or not user.is_verified:
            raise HTTPException(status_code=403, detail="Verified account required")
    try:
        authorization_url = await get_telegram_oidc_service().start(
            purpose=purpose,
            return_to=return_to,
            user_id=user_id,
        )
    except TelegramOIDCError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(authorization_url, status_code=303)


@router.get("/telegram/callback", include_in_schema=False)
async def telegram_callback(
    request: Request,
    state: str = Query(min_length=20, max_length=256),
    code: str | None = Query(default=None, min_length=1, max_length=4096),
    error: str | None = Query(default=None, max_length=255),
) -> RedirectResponse:
    await _enforce_telegram_rate_limit(request)
    service = get_telegram_oidc_service()
    transaction = None
    try:
        transaction = await service.consume(state)
        if error or not code:
            raise TelegramOIDCError("Telegram login was cancelled")
        claims = await service.exchange_and_validate(code=code, transaction=transaction)
        phone = str(claims["phone_number"])
        if transaction.purpose == "signin":
            user = await service.find_signin_user(phone)
            await create_browser_session(request, user.id, client="telegram-oidc")
        else:
            session = await get_session(request, session_required=False)
            if session is None or session.get_user_id() != transaction.user_id:
                raise TelegramOIDCError("The Lemma session changed during verification")
            await service.verify_mobile(UUID(transaction.user_id), phone)  # type: ignore[arg-type]
        return RedirectResponse(transaction.return_to, status_code=303)
    except TelegramOIDCError:
        return _telegram_error_redirect(
            transaction.return_to if transaction is not None else None,
            "unable_to_authenticate",
        )


@router.post("/email/bounces", include_in_schema=False, status_code=204)
async def accept_email_bounce(request: Request, event: BounceEvent) -> Response:
    secret = reveal_secret(settings.auth_bounce_webhook_secret)
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")
    timestamp = request.headers.get("x-lemma-timestamp", "")
    signature = request.headers.get("x-lemma-signature", "")
    body = await request.body()
    verify_bounce_signature(
        timestamp=timestamp,
        signature=signature,
        body=body,
        secret=secret,
    )
    if event.event == "soft_bounce":
        return Response(status_code=204)

    email = normalize_identity_email(str(event.email))
    async with async_session_maker() as db_session:
        user = await db_session.scalar(
            select(User).where(func.lower(User.email) == email)
        )
        if user is not None and user.is_active and not user.is_deleted:
            user.is_active = False
            user.deactivated_at = user.deactivated_at or datetime.now(timezone.utc)
            user.deactivation_reason = "HARD_BOUNCE"
            await db_session.commit()
            await get_user_cache().invalidate(user.id)
            await revoke_all_sessions_for_user(str(user.id))
    return Response(status_code=204)


@router.get(
    "/verify-token",
    operation_id="auth.verify_token",
    summary="Verify access token",
    description="Validate the current bearer token and return the resolved user context.",
    response_model=VerifyTokenResponse,
)
async def verify_token(
    request: Request,
    user_service: UserServiceDep,
    pod_membership: PodMembershipDep,
) -> VerifyTokenResponse:
    user: UserEntity = request.state.user
    user_data = await user_service.get_user(user.id)
    auth_claims = getattr(request.state, "auth_claims", {}) or {}
    pod_id = auth_claims.get(CLAIM_POD_ID)
    if pod_id is not None:
        pod_id = UUID(str(pod_id))
    scopes = auth_claims.get(CLAIM_SCOPE)
    if isinstance(scopes, str):
        scopes = [scopes]
    if not isinstance(scopes, list) or not all(
        isinstance(scope, str) for scope in scopes
    ):
        scopes = []
    function_id = None
    function_name = None
    if auth_claims.get(CLAIM_ACTOR_TYPE) == WorkloadPrincipalType.FUNCTION.value:
        raw_function_id = auth_claims.get(CLAIM_ACTOR_ID)
        function_id = (
            UUID(str(raw_function_id)) if raw_function_id is not None else None
        )
        function_name = auth_claims.get(CLAIM_ACTOR_NAME)
    organization_id = (
        await pod_membership.get_pod_organization_id(pod_id)
        if pod_id is not None
        else None
    )
    return VerifyTokenResponse(
        user_id=user.id,
        email=user_data.email,
        pod_id=pod_id,
        organization_id=organization_id,
        function_id=function_id,
        function_name=function_name if isinstance(function_name, str) else None,
        scopes=scopes,
    )


@router.get(
    "/cli/info",
    include_in_schema=False,
    operation_id="auth.cli.info",
    summary="Get CLI auth configuration",
    description="Return the frontend and API URLs the Lemma CLI should use for browser-based login.",
    response_model=CliAuthInfoResponse,
)
async def cli_auth_info() -> CliAuthInfoResponse:
    return CliAuthInfoResponse(
        api_url=settings.cli_api_url or settings.api_url,
        auth_frontend_url=settings.cli_auth_frontend_url or settings.auth_frontend_url,
    )


@router.post(
    "/desktop/requests",
    include_in_schema=False,
    operation_id="auth.desktop.request.create",
    response_model=DesktopAuthRequestResponse,
)
async def create_desktop_auth_request(
    body: DesktopAuthRequestCreate,
    request: Request,
) -> DesktopAuthRequestResponse:
    client_key = request.client.host if request.client else "unknown"
    try:
        handoff = await get_desktop_auth_handoff_store().create(
            body.code_challenge,
            client_key=client_key,
        )
    except DesktopAuthRateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many desktop login requests",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    return DesktopAuthRequestResponse(
        request_id=handoff.request_id,
        expires_in_seconds=handoff.expires_in_seconds,
    )


@router.post(
    "/desktop/requests/{request_id}/complete",
    include_in_schema=False,
    operation_id="auth.desktop.request.complete",
    response_model=DesktopAuthCompleteResponse,
)
async def complete_desktop_auth_request(
    request_id: str,
    request: Request,
) -> DesktopAuthCompleteResponse:
    user: UserEntity = request.state.user
    try:
        await get_desktop_auth_handoff_store().complete(request_id, user.id)
    except DesktopAuthRequestNotFound as exc:
        raise HTTPException(
            status_code=404, detail="Desktop login request expired"
        ) from exc
    except DesktopAuthCompletionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Desktop login request was already completed by another user",
        ) from exc
    return DesktopAuthCompleteResponse()


@router.post(
    "/desktop/session",
    include_in_schema=False,
    operation_id="auth.desktop.session.create",
    response_model=DesktopAuthSessionResponse,
)
async def create_desktop_auth_session(
    body: DesktopAuthSessionRequest,
    request: Request,
) -> DesktopAuthSessionResponse:
    if request.headers.get("st-auth-mode") != "cookie":
        raise HTTPException(
            status_code=400,
            detail="Desktop session exchange requires cookie auth mode",
        )
    try:
        user_id = await get_desktop_auth_handoff_store().consume(
            body.request_id,
            body.code_verifier,
        )
    except DesktopAuthRequestPending as exc:
        raise HTTPException(
            status_code=409, detail="Desktop login is still pending"
        ) from exc
    except DesktopAuthVerifierRejected as exc:
        raise HTTPException(
            status_code=403, detail="Desktop login verifier was rejected"
        ) from exc
    except DesktopAuthRequestNotFound as exc:
        raise HTTPException(
            status_code=404, detail="Desktop login request expired"
        ) from exc

    session_handle = await create_desktop_browser_session(request, user_id)
    return DesktopAuthSessionResponse(
        user_id=user_id,
        session_handle=session_handle,
    )


@router.post(
    "/cli/session-tokens",
    include_in_schema=False,
    operation_id="auth.cli.session_tokens",
    summary="Mint a CLI session from the current browser session",
    description="Create a dedicated Lemma CLI session for the current authenticated user and return access and refresh tokens.",
    response_model=CliSessionResponse,
)
async def cli_session_tokens(
    request: Request,
    user_service: UserServiceDep,
) -> CliSessionResponse:
    user: UserEntity = request.state.user
    user_data = await user_service.get_user(user.id)
    session_payload = await create_cli_session_tokens(
        user.id,
        access_token_payload={"client": "lemma-cli"},
        session_data={"client": "lemma-cli"},
    )
    return CliSessionResponse(
        **session_payload,
        email=user_data.email,
    )


@router.post(
    "/cli/refresh",
    include_in_schema=False,
    operation_id="auth.cli.refresh",
    summary="Refresh a CLI session",
    description="Refresh a CLI access token using a previously issued refresh token.",
    response_model=CliSessionResponse,
)
async def cli_refresh_session(
    body: CliRefreshRequest,
    user_service: UserServiceDep,
) -> CliSessionResponse:
    try:
        session_payload = await refresh_cli_session_tokens(body.refresh_token)
        user_id = UUID(str(session_payload["user_id"]))
        user_data = await user_service.get_user(user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_REFRESH_TOKEN",
                "message": "Unable to refresh CLI session.",
                "details": {"error_type": type(exc).__name__},
            },
        ) from exc

    return CliSessionResponse(
        **session_payload,
        email=user_data.email,
    )
