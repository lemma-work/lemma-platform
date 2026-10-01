"""Browser-bound access to one app origin's assets."""

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from supertokens_python.exceptions import SuperTokensError

from app.core.api.dependencies import CurrentUser
from app.core.api.schemas import ErrorResponse
from app.core.config import settings
from app.core.domain.errors import DomainError
from app.modules.apps.api.dependencies import AppUseCasesDep
from app.modules.apps.api.host_routing import app_label_from_host, split_release_label
from app.modules.apps.domain.access import (
    AppAccessInvalidError,
    AppAccessRequest,
    AppAccessSession,
    AppAccessUnavailableError,
)
from app.modules.apps.domain.entities import public_app_url
from app.modules.apps.domain.errors import AppNotFoundError
from app.modules.apps.services.app_access_store import (
    AppAccessStore,
    CODE_TTL_SECONDS,
    REQUEST_TTL_SECONDS,
    token_hash,
)
from app.modules.identity.contracts.app_sessions import (
    app_session_parent_is_active,
    browser_client_ip,
)

router = APIRouter(tags=["Apps"], redirect_slashes=False)
ACCESS_COOKIE = "__Host-lemmaAppAccess"
BINDING_COOKIE_PREFIX = "__Host-lemmaAppAccessBinding-"
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}


class AppAccessCreateRequest(BaseModel):
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class AppAccessRequestResponse(BaseModel):
    request_id: str
    expires_in_seconds: int


class AppAccessAuthorizeRequest(BaseModel):
    app_origin: str = Field(max_length=512)


class AppAccessAuthorizeResponse(BaseModel):
    code: str
    expires_in_seconds: int


class AppAccessRedeemRequest(BaseModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    verifier: str = Field(pattern=r"^[A-Za-z0-9._~-]{43,128}$")


class AppAccessRedeemResponse(BaseModel):
    expires_at: int


def get_app_access_store() -> AppAccessStore:
    return AppAccessStore()


AppAccessStoreDep = Annotated[AppAccessStore, Depends(get_app_access_store)]


def binding_cookie_name(request_id: str) -> str:
    return BINDING_COOKIE_PREFIX + request_id


def app_origin(request: Request) -> tuple[str, str, str | None]:
    label = app_label_from_host(request.headers.get("host", ""))
    origin = public_app_url(label) if label else None
    if (
        not origin
        or not origin.startswith("https://")
        or request.headers.get("host", "").lower() != urlsplit(origin).netloc
    ):
        raise AppAccessInvalidError()
    slug, release_ref = split_release_label(label or "")
    assert slug is not None
    return origin, slug, release_ref


def error_response(error: DomainError) -> JSONResponse:
    return JSONResponse(
        {"message": error.message, "code": error.code},
        status_code=error.status_code,
        headers=PRIVATE_HEADERS,
    )


def _require_same_origin(request: Request, origin: str) -> None:
    if request.headers.get("origin") != origin:
        raise AppAccessInvalidError()


@router.post(
    "/_lemma/app-access/requests",
    response_model=AppAccessRequestResponse,
    operation_id="app.access.request.create",
    responses={
        401: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def create_app_access_request(
    request: Request, data: AppAccessCreateRequest, store: AppAccessStoreDep
) -> Response:
    try:
        origin, slug, release_ref = app_origin(request)
        _require_same_origin(request, origin)
        # First visits in separate tabs have no shared cookie yet. A binding
        # for each request lets both responses arrive without overwriting it.
        binding = secrets.token_urlsafe(32)
        pending = AppAccessRequest(
            origin=origin,
            slug=slug,
            release_ref=release_ref,
            challenge=data.challenge,
            binding_hash=token_hash(binding),
        )
        request_id = await asyncio.wait_for(
            store.create(pending, client_key=browser_client_ip(request.scope)),
            timeout=5,
        )
    except DomainError as error:
        return error_response(error)
    except RedisError, TimeoutError:
        return error_response(AppAccessUnavailableError())
    response = JSONResponse(
        AppAccessRequestResponse(
            request_id=request_id, expires_in_seconds=REQUEST_TTL_SECONDS
        ).model_dump(),
        headers=PRIVATE_HEADERS,
    )
    response.set_cookie(
        binding_cookie_name(request_id),
        binding,
        max_age=REQUEST_TTL_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.post(
    "/apps/access/requests/{request_id}/authorize",
    response_model=AppAccessAuthorizeResponse,
    operation_id="app.access.request.authorize",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def authorize_app_access_request(
    request: Request,
    request_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{43}$")],
    data: AppAccessAuthorizeRequest,
    user: CurrentUser,
    use_cases: AppUseCasesDep,
    store: AppAccessStoreDep,
) -> Response:
    try:
        if getattr(request.state, "delegation_claims", None) is not None:
            raise AppAccessInvalidError()
        pending = await asyncio.wait_for(store.get_request(request_id), timeout=5)
        workspace_origin = urlsplit(settings.frontend_url)
        allowed_origins = {
            pending.origin,
            f"{workspace_origin.scheme}://{workspace_origin.netloc}",
        }
        if (
            data.app_origin != pending.origin
            or request.headers.get("origin") not in allowed_origins
        ):
            raise AppAccessInvalidError()
        target = await use_cases.authorize_host_access(
            slug=pending.slug,
            release_ref=pending.release_ref,
            request=request,
            user_id=user.id,
        )
        parent = request.state.session
        expires_at = int(await asyncio.wait_for(parent.get_expiry(), timeout=5) / 1000)
        if expires_at <= int(time.time()):
            raise AppAccessInvalidError()
        session = AppAccessSession(
            **target.model_dump(),
            user_id=user.id,
            origin=pending.origin,
            slug=pending.slug,
            release_ref=pending.release_ref,
            parent_handle=parent.get_handle(),
            expires_at=expires_at,
        )
        code = await asyncio.wait_for(
            store.authorize(request_id, pending, session), timeout=5
        )
    except DomainError as error:
        return error_response(
            AppNotFoundError() if error.status_code in {403, 404, 410} else error
        )
    except RedisError, SuperTokensError, TimeoutError:
        return error_response(AppAccessUnavailableError())
    return JSONResponse(
        AppAccessAuthorizeResponse(
            code=code, expires_in_seconds=CODE_TTL_SECONDS
        ).model_dump(),
        headers=PRIVATE_HEADERS,
    )


@router.post(
    "/_lemma/app-access/redeem",
    response_model=AppAccessRedeemResponse,
    operation_id="app.access.redeem",
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def redeem_app_access(
    request: Request, data: AppAccessRedeemRequest, store: AppAccessStoreDep
) -> Response:
    try:
        origin, _slug, _release_ref = app_origin(request)
        _require_same_origin(request, origin)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(data.verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        token, session = await asyncio.wait_for(
            store.redeem(
                request_id=data.request_id,
                code=data.code,
                challenge=challenge,
                binding=request.cookies.get(binding_cookie_name(data.request_id), ""),
                origin=origin,
            ),
            timeout=5,
        )
        if not await app_session_parent_is_active(
            session.parent_handle, session.user_id
        ):
            raise AppAccessInvalidError()
    except DomainError as error:
        return error_response(error)
    except RedisError, SuperTokensError, TimeoutError:
        return error_response(AppAccessUnavailableError())
    response = JSONResponse(
        AppAccessRedeemResponse(expires_at=session.expires_at).model_dump(),
        headers=PRIVATE_HEADERS,
    )
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=max(1, session.expires_at - int(time.time())),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        binding_cookie_name(data.request_id),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response
