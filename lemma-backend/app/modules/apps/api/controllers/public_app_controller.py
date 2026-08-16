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
from app.modules.apps.api.host_routing import split_release_label

router = APIRouter(
    prefix="/public/apps",
    tags=["Public Apps"],
    redirect_slashes=False,
)

_SLUG_HEADER = "X-App-Public-Slug"


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
    slug, release_ref = _get_slug(request)
    asset = await use_cases.serve_public_asset(
        slug=slug,
        asset_path=None,
        request_etag=request.headers.get("if-none-match"),
        release_ref=release_ref,
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
    slug, release_ref = _get_slug(request)
    asset = await use_cases.serve_public_asset(
        slug=slug,
        asset_path=asset_path or None,
        request_etag=request.headers.get("if-none-match"),
        release_ref=release_ref,
    )
    return app_asset_response(asset)
