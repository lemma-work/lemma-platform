"""Host asset controller -- anonymous public builds and permitted private ones.

Apps are served by host: ``<public_slug>.<app_base_domain>``. Requests reach
this router at /public/apps only through ``AppHostRoutingMiddleware``, which
derives the app's label from the request Host and hands it over as the
``X-App-Public-Slug`` header -- after dropping any copy the client sent.

A private app is served on its hosted HTTPS address only to someone holding its
access cookie and still allowed to read it; anyone else -- and anyone asking for
a slug that does not exist -- gets the same sign-in page. See
``app_access_controller`` for how the cookie is obtained.
"""

from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from supertokens_python.exceptions import SuperTokensError

from app.modules.apps.api.app_access import (
    ACCESS_COOKIE,
    PRIVATE_NO_STORE,
    AppHost,
    private_app_host,
    private_error,
)
from app.modules.apps.api.app_access_page import render_app_access_page
from app.modules.apps.api.asset_not_found_page import render_asset_not_found_page
from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.api.dependencies import AppUseCasesDep
from app.modules.apps.api.host_routing import split_release_label
from app.modules.apps.domain.errors import (
    AppAccessInvalidError,
    AppAccessUnavailableError,
    AppAssetNotFoundError,
    AppNotFoundError,
)
from app.modules.apps.services.app_access import (
    AppAccessPurpose,
    verify_app_access_token,
)

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
    ``orders--r7`` previews release 7. The host routing middleware sets it from
    the Host, so a preview host needs no ingress configuration of its own.

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


async def _viewer_app_id(
    request: Request,
    use_cases: AppUseCasesDep,
    host: AppHost | None,
    slug: str,
    release_ref: str | None,
) -> UUID | AppAccessUnavailableError | None:
    """The app this request's access cookie may read, if any.

    The unavailable error is returned, not raised: a public app must still be
    served when the session service is down, so it only matters once the slug
    turns out to need a viewer.
    """
    claims = (
        verify_app_access_token(
            request.cookies.get(ACCESS_COOKIE),
            purpose=AppAccessPurpose.COOKIE,
            origin=host.origin,
        )
        if host is not None
        else None
    )
    if claims is None:
        return None
    try:
        allowed = await use_cases.authorize_host_viewer(
            claims, slug=slug, release_ref=release_ref, request=request
        )
    except SuperTokensError, TimeoutError:
        return AppAccessUnavailableError()
    return claims.app_id if allowed else None


async def _serve_host_asset(
    request: Request, use_cases: AppUseCasesDep, asset_path: str | None
) -> Response:
    slug, release_ref = _get_slug(request)
    host = private_app_host(request.headers.get("host", ""))
    viewer = await _viewer_app_id(request, use_cases, host, slug, release_ref)
    asset = await use_cases.serve_public_asset(
        slug=slug,
        release_ref=release_ref,
        asset_path=asset_path,
        request_etag=request.headers.get("if-none-match"),
        viewer_app_id=viewer if isinstance(viewer, UUID) else None,
    )
    if asset is not None:
        return app_asset_response(asset)
    if host is None:
        # Not a hosted HTTPS app address (the desktop, or the API host with a
        # slug header): only published apps exist here, as they always have.
        raise AppNotFoundError(f"App with public slug '{slug}' not found")
    if isinstance(viewer, AppAccessUnavailableError):
        return private_error(viewer)
    return _access_required(request, asset_path)


def _access_required(request: Request, asset_path: str | None) -> Response:
    """The sign-in page for a person, a bare 401 for code -- the same for every slug.

    A refused cookie is left in place: the sign-in page replaces it, and one
    refused while access was briefly withdrawn works again once it is restored.
    """
    if not _can_show_access_page(request, asset_path):
        return private_error(AppAccessInvalidError())
    return Response(
        render_app_access_page(),
        status_code=401,
        media_type="text/html",
        headers=PRIVATE_NO_STORE,
    )


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
    return await _serve_host_asset(request, use_cases, None)


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
    try:
        return await _serve_host_asset(request, use_cases, asset_path or None)
    except AppAssetNotFoundError as error:
        if not _is_navigation(request):
            raise
        return _asset_not_found_response(request, error)
