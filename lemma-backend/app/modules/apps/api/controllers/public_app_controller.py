"""Host asset controller — anonymous public builds and authenticated private builds.

Apps are served by host: ``<public_slug>.<app_base_domain>``. The public slug
always arrives as the ``X-App-Public-Slug`` header — injected by the cloud nginx
ingress (app_ingress.yaml), or locally by ``AppHostRoutingMiddleware`` which
derives it from the request Host. Requests reach this router at /public/apps
either via that host rewrite or directly from clients that set the header.
"""

import asyncio
from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.modules.apps.api.asset_not_found_page import render_asset_not_found_page
from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.api.dependencies import AppUseCasesDep
from app.modules.apps.api.host_routing import split_release_label
from app.modules.apps.domain.errors import AppAssetNotFoundError, AppNotFoundError
from app.core.config import settings
from app.modules.apps.api.host_routing import app_label_from_host
from app.core.domain.errors import DomainError
from app.modules.apps.domain.access import (
    AppAccessInvalidError,
    AppAccessRequiredError,
    AppAccessUnavailableError,
)
from app.modules.apps.api.app_access_page import render_app_access_page
from app.modules.apps.api.controllers.app_access_controller import (
    ACCESS_COOKIE,
    PRIVATE_HEADERS,
    AppAccessStoreDep,
    app_origin,
    error_response,
)
from app.modules.identity.contracts.app_sessions import app_session_parent_is_active
from redis.exceptions import RedisError
from supertokens_python.exceptions import SuperTokensError

router = APIRouter(
    prefix="/public/apps",
    tags=["Public Apps"],
    redirect_slashes=False,
)

_SLUG_HEADER = "X-App-Public-Slug"


def _is_navigation(request: Request) -> bool:
    """Whether a person is looking at this response, or code is reading it.

    Fetch metadata distinguishes documents from scripts even when a caller
    sends an HTML Accept header. Older clients fall back to Accept.
    """
    destination = request.headers.get("sec-fetch-dest")
    return "text/html" in request.headers.get("accept", "") and (
        destination is None or destination in {"document", "iframe", "frame"}
    )


def _can_show_access_page(request: Request, asset_path: str | None) -> bool:
    if not _is_navigation(request):
        return False
    if request.headers.get("sec-fetch-dest") in {"document", "iframe", "frame"}:
        return True
    return PurePosixPath(asset_path or "").suffix.lower() in {"", ".html", ".htm"}


def _asset_not_found_response(
    request: Request, error: AppAssetNotFoundError
) -> Response:
    """A dead end a person can act on, for the requests that have a person."""
    return Response(
        content=render_asset_not_found_page(
            asset_path=error.asset_path,
            pod_id=error.pod_id,
        ),
        status_code=404,
        media_type="text/html; charset=utf-8",
        # The page names one pod file path; nothing should keep it, and a
        # search engine indexing an app's broken links helps nobody.
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


def _get_slug(request: Request) -> tuple[str, str | None]:
    """Resolve ``(slug, release_ref)`` from the one header that carries both.

    The label is the whole mechanism: ``orders`` serves what is live,
    ``orders--r7`` previews release 7. Both deployments hand it over the same
    way -- the cloud nginx ingress resolves the label from the host and forwards
    it intact, and the local middleware sets it from the host itself -- so
    previews work on the existing ingress with no config change.

    There is deliberately no separate release header. One existed, nothing
    upstream ever set it, and a client could therefore supply its own and pin
    the canonical live host to a superseded build.
    """
    raw = request.headers.get(_SLUG_HEADER, "").strip().lower()
    if not raw:
        raise HTTPException(status_code=400, detail="Missing app slug")
    slug, release_ref = split_release_label(raw)
    if not slug:
        raise HTTPException(status_code=400, detail="Missing app slug")
    return slug, release_ref


async def _serve_host_asset(
    request: Request,
    use_cases: AppUseCasesDep,
    store: AppAccessStoreDep,
    asset_path: str | None,
) -> Response:
    slug, release_ref = _get_slug(request)
    try:
        asset = await use_cases.serve_public_asset(
            slug=slug,
            release_ref=release_ref,
            asset_path=asset_path,
            request_etag=request.headers.get("if-none-match"),
        )
        return app_asset_response(asset)
    except AppAccessRequiredError:
        pass
    if (
        not settings.api_url.startswith("https://")
        or app_label_from_host(request.headers.get("host", "")) is None
    ):
        raise AppAccessRequiredError()
    return await _serve_private_asset(request, use_cases, store, asset_path)


async def _serve_private_asset(
    request: Request,
    use_cases: AppUseCasesDep,
    store: AppAccessStoreDep,
    asset_path: str | None,
) -> Response:
    # Local desktop keeps its existing routing and auth contract. Host-scoped
    # cookies here are specifically for a hosted HTTPS app origin.
    try:
        origin, host_slug, host_release = app_origin(request)
        token = request.cookies.get(ACCESS_COOKIE)
        if not token:
            raise AppAccessInvalidError()
        access = await asyncio.wait_for(
            store.get_session(token, origin=origin), timeout=5
        )
        if access.slug != host_slug or access.release_ref != host_release:
            raise AppAccessInvalidError()
        if not await app_session_parent_is_active(access.parent_handle, access.user_id):
            raise AppAccessInvalidError()
        asset = await use_cases.serve_private_host_asset(
            access=access, request=request, asset_path=asset_path
        )
        return app_asset_response(asset)
    except AppAccessInvalidError as error:
        if not _can_show_access_page(request, asset_path):
            return error_response(error)
        response = Response(
            render_app_access_page(),
            status_code=401,
            media_type="text/html",
            headers=PRIVATE_HEADERS,
        )
        response.delete_cookie(
            ACCESS_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
        )
        return response
    except AppAssetNotFoundError:
        raise
    except DomainError:
        return error_response(AppNotFoundError())
    except RedisError, SuperTokensError, TimeoutError:
        return error_response(AppAccessUnavailableError())


@router.get(
    "",
    status_code=200,
    operation_id="public.app.root",
    summary="Get App Root Asset",
    include_in_schema=False,
)
async def get_app_root(
    request: Request,
    use_cases: AppUseCasesDep,
    store: AppAccessStoreDep,
) -> Response:
    return await _serve_host_asset(request, use_cases, store, None)


@router.get(
    "/{asset_path:path}",
    status_code=200,
    operation_id="public.app.asset",
    summary="Get App Asset by Slug",
    include_in_schema=False,
)
async def get_app_asset_by_slug(
    request: Request,
    asset_path: str,
    use_cases: AppUseCasesDep,
    store: AppAccessStoreDep,
) -> Response:
    try:
        return await _serve_host_asset(request, use_cases, store, asset_path or None)
    except AppAssetNotFoundError as error:
        if not _is_navigation(request):
            raise
        return _asset_not_found_response(request, error)
