from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from app.core.config import settings
from app.modules.identity.api.dependencies import PodMembershipDep, UserServiceDep
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    create_cli_session_tokens,
    create_desktop_browser_session,
    refresh_cli_session_tokens,
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
