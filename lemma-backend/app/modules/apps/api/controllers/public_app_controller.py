"""Public app asset controller — serves app builds by public slug (unauthenticated).

Apps are served by host: ``<public_slug>.<app_base_domain>``. The public slug
always arrives as the ``X-App-Public-Slug`` header — injected by the cloud nginx
ingress (app_ingress.yaml), or locally by ``AppHostRoutingMiddleware`` which
derives it from the request Host. Requests reach this router at /public/apps
either via that host rewrite or directly from clients that set the header.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.api.dependencies import build_app_service
from app.modules.apps.domain.entities import AppAssetDocument

router = APIRouter(
    prefix="/public/apps",
    tags=["Public Apps"],
    redirect_slashes=False,
)

_SLUG_HEADER = "X-App-Public-Slug"


def _get_slug(request: Request) -> str:
    slug = request.headers.get(_SLUG_HEADER, "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="Missing app slug")
    return slug


async def _serve_public_asset(
    *,
    slug: str,
    asset_path: str | None,
    request_etag: str | None,
    uow_factory: UnitOfWorkFactory,
) -> Response:
    # Resolve in a short UoW (released here), then read asset bytes from storage
    # with no pooled connection held — a request-scoped AppServiceDep would pin a
    # connection across the whole response while reading from storage. This is the
    # highest-traffic path (every app page load + static asset, by host).
    async with uow_factory() as uow:
        app_service = build_app_service(uow)
        resolved = await app_service.resolve_app_asset_by_public_slug(
            slug, asset_path=asset_path, request_etag=request_etag
        )
    if isinstance(resolved, AppAssetDocument):
        return app_asset_response(resolved)
    asset = await app_service.read_app_asset(resolved)
    return app_asset_response(asset)


@router.get(
    "",
    status_code=200,
    operation_id="public.app.root",
    summary="Get App Root Asset",
    include_in_schema=False,
)
async def get_app_root(
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response:
    return await _serve_public_asset(
        slug=_get_slug(request),
        asset_path=None,
        request_etag=request.headers.get("if-none-match"),
        uow_factory=uow_factory,
    )


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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response:
    return await _serve_public_asset(
        slug=_get_slug(request),
        asset_path=asset_path or None,
        request_etag=request.headers.get("if-none-match"),
        uow_factory=uow_factory,
    )
