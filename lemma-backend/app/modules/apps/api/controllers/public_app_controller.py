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
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from supertokens_python.exceptions import SuperTokensError

from app.core.config import settings
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
# Host-only cookies the SuperTokens browser SDK keeps on each app host.
_SESSION_MARKER = "st-last-access-token-update"
_FRONT_TOKEN = "sFrontToken"
_FROM_THIS_SITE = {"same-site", "same-origin"}


def _loads_a_page(request: Request) -> bool:
    """Whether fetch metadata says this request is a page load.

    The mode is the half that survives a service worker. Every hosted app runs
    the install worker, which forwards each navigation with
    ``fetch(event.request)``, and that resets the destination to ``empty``
    (w3c/ServiceWorker#1803) but keeps ``Sec-Fetch-Mode: navigate`` -- a mode
    page script cannot ask for. Reading the destination alone sent a lapsed
    cookie the bare 401 a script gets instead of the sign-in page that renews
    it, and nothing short of clearing the site's data got the person back in.
    """
    return request.headers.get("sec-fetch-mode") == "navigate" or (
        request.headers.get("sec-fetch-dest") in {"document", "iframe", "frame"}
    )


def _is_navigation(request: Request) -> bool:
    """Whether a person is looking at this response, or code is reading it.

    Fetch metadata distinguishes documents from scripts even when a caller
    sends an HTML Accept header. Older clients fall back to Accept.
    """
    return "text/html" in request.headers.get("accept", "") and (
        request.headers.get("sec-fetch-dest") is None or _loads_a_page(request)
    )


def _can_show_access_page(request: Request, asset_path: str | None) -> bool:
    if not _is_navigation(request):
        return False
    if _loads_a_page(request):
        return True
    return _suffix(asset_path) in {"", ".html", ".htm"}


def _suffix(asset_path: str | None) -> str:
    return PurePosixPath(asset_path or "").suffix.lower()


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


def _expire_stale_session_marker(request: Request, response: Response) -> None:
    """Drop the session marker a failed refresh left behind on this app host.

    SuperTokens' browser SDK keeps ``sFrontToken`` and
    ``st-last-access-token-update`` on each app host. A failed refresh expires
    the first and keeps the second, which never expires, and from then on
    ``doesSessionExist()`` on that host answers "no" from the marker without
    asking. Signing in again renews the shared HttpOnly cookies but cannot
    reach a marker on the app's host, so the app sent a signed-in person to
    the portal, which saw the session and sent them straight back.

    Without the marker the SDK asks the server once: a live session comes back
    with a new front token, and an ended one is refused as before. It is done
    here, on the page load, because each app bundles the SDK it was built
    with; a fix in the SDK reaches a bundled app only when it is rebuilt.

    Only for a page reached the loop's way (see ``_came_the_loops_way``). A
    visitor who typed the address or came from a search pays no refused
    refresh for it; a signed-in one on a half-cleared host is shown the app's
    sign-in once, and the trip back from the portal repairs it.
    """
    cookies = request.cookies
    if _SESSION_MARKER not in cookies or _FRONT_TOKEN in cookies:
        return
    if not _came_the_loops_way(request):
        return
    # SameSite=None, so a frame on another site takes the deletion too; Secure
    # is what None requires, and what an HTTPS app host already is.
    secure = settings.api_url.startswith("https://")
    response.delete_cookie(
        _SESSION_MARKER,
        path="/",
        secure=secure,
        samesite="none" if secure else "lax",
    )
    # A response that sets a cookie is this browser's alone.
    response.headers["Cache-Control"] = "private, no-cache"


def _came_the_loops_way(request: Request) -> bool:
    """Back from the portal, framed by the workspace, or from the app itself.

    The app itself covers its own links, its access page's reload, and a
    navigation its install worker forwarded. Fetch metadata says so when the
    app shares a registrable domain with the workspace, which hosted Lemma
    does. Self-hosting may give apps a registrable domain of their own
    (``docs/self-hosting.md``), and then the trip back from the portal and the
    workspace's frame are both cross-site; their Referer still names the
    portal. A browser that predates fetch metadata keeps the repair.
    """
    if request.headers.get("sec-fetch-site", "same-site") in _FROM_THIS_SITE:
        return True
    portal = {_origin(settings.frontend_url), _origin(settings.auth_frontend_url)}
    referrer = _origin(request.headers.get("referer"))
    return referrer is not None and referrer in portal


def _origin(url: str | None) -> str | None:
    parts = urlsplit(url or "")
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


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
        response = app_asset_response(asset)
        if _is_navigation(request) and (
            asset.is_entrypoint or _suffix(asset_path) in {".html", ".htm"}
        ):
            _expire_stale_session_marker(request, response)
        return response
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
