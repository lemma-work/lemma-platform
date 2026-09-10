"""Public app asset controller — serves app builds by public slug (unauthenticated).

Apps are served by host: ``<public_slug>.<app_base_domain>``. The public slug
always arrives as the ``X-App-Public-Slug`` header — injected by the cloud nginx
ingress (app_ingress.yaml), or locally by ``AppHostRoutingMiddleware`` which
derives it from the request Host. Requests reach this router at /public/apps
either via that host rewrite or directly from clients that set the header.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.modules.apps.api.asset_not_found_page import render_asset_not_found_page
from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.api.dependencies import AppUseCasesDep
from app.modules.apps.api.host_routing import split_release_label
from app.modules.apps.domain.errors import AppAssetNotFoundError

router = APIRouter(
    prefix="/public/apps",
    tags=["Public Apps"],
    redirect_slashes=False,
)

_SLUG_HEADER = "X-App-Public-Slug"


def _is_navigation(request: Request) -> bool:
    """Whether a person is looking at this response, or code is reading it.

    A fetch for a missing bundle asset still gets the JSON error it has always
    got; only a browser following a link is shown a page. `Accept` is the
    signal every navigation sends and no asset fetch does.
    """
    return "text/html" in request.headers.get("accept", "")


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
    try:
        asset = await use_cases.serve_public_asset(
            slug=slug,
            release_ref=release_ref,
            asset_path=asset_path or None,
            request_etag=request.headers.get("if-none-match"),
        )
    except AppAssetNotFoundError as error:
        if not _is_navigation(request):
            raise
        return _asset_not_found_response(request, error)
    return app_asset_response(asset)
