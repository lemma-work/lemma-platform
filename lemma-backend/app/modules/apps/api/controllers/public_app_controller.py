"""Public app asset controller — serves app builds by public slug (unauthenticated).

Apps are served by host: ``<public_slug>.<app_base_domain>``. The public slug
always arrives as the ``X-App-Public-Slug`` header — injected by the cloud nginx
ingress (app_ingress.yaml), or locally by ``AppHostRoutingMiddleware`` which
derives it from the request Host. Requests reach this router at /public/apps
either via that host rewrite or directly from clients that set the header.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.api.dependencies import AppUseCasesDep

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
) -> Response:
    asset = await use_cases.serve_public_asset(
        slug=_get_slug(request),
        asset_path=None,
        request_etag=request.headers.get("if-none-match"),
    )
    return app_asset_response(asset)


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
) -> Response:
    asset = await use_cases.serve_public_asset(
        slug=_get_slug(request),
        asset_path=asset_path or None,
        request_etag=request.headers.get("if-none-match"),
    )
    return app_asset_response(asset)
