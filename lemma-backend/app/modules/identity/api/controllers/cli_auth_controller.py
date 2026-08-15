"""CLI session endpoints.

Split out of ``auth_controller`` rather than left in it. These two are a
distinct client surface -- CLI-only, deliberately absent from the public schema
-- and the file they came from sits at the size ceiling the architecture ratchet
enforces. Extraction is the way past that ceiling; re-baselining is not.

Both mint or refresh a SuperTokens session, which is an HTTP round trip, and
neither takes a request-scoped unit of work. The one database lookup each needs
opens its own short scope, so no pooled connection is held across the token
exchange -- on `/cli/refresh` the exchange happens *before* the database is
consulted at all, so the old request-scoped session was held across it for
nothing.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.core.api.dependencies import get_uow_factory
from app.core.authorization.scope import uow_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.identity.api.dependencies import get_user_service
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    create_cli_session_tokens,
    refresh_cli_session_tokens,
)

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


router = APIRouter(prefix="/auth", tags=["Auth"])


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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> CliSessionResponse:
    user: UserEntity = request.state.user
    # The lookup gets its own scope so that minting the session -- a SuperTokens
    # round trip over HTTP -- does not run with a pooled connection checked out.
    async with uow_scope(uow_factory) as uow:
        user_data = await get_user_service(uow).get_user(user.id)
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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> CliSessionResponse:
    try:
        # The refresh is a SuperTokens round trip and comes first, so a
        # request-scoped session was held across it for no reason at all -- the
        # database is not consulted until the token has already been accepted.
        session_payload = await refresh_cli_session_tokens(body.refresh_token)
        user_id = UUID(str(session_payload["user_id"]))
        async with uow_scope(uow_factory) as uow:
            user_data = await get_user_service(uow).get_user(user_id)
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
