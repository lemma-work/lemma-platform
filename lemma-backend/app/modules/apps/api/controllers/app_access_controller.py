"""Open a private app on its own host: a ticket from the API, a cookie on the host.

Only the app host's sign-in page calls these, so neither is in the API schema.
See ``services/app_access.py`` for why two signed tokens are enough.
"""

import time
from dataclasses import replace
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser
from app.core.domain.errors import DomainError
from app.modules.apps.api.app_access import (
    ACCESS_COOKIE,
    PRIVATE_NO_STORE,
    private_app_host,
    private_error,
)
from app.modules.apps.api.dependencies import AppUseCasesDep
from app.modules.apps.domain.errors import AppAccessInvalidError, AppNotFoundError
from app.modules.apps.services.app_access import (
    COOKIE_TTL_SECONDS,
    TICKET_TTL_SECONDS,
    AppAccessClaims,
    AppAccessPurpose,
    mint_app_access_token,
    verify_app_access_token,
)

router = APIRouter(tags=["Apps"], redirect_slashes=False)


class AppAccessRedeemRequest(BaseModel):
    ticket: str = Field(min_length=1, max_length=2048)


@router.post(
    "/apps/access/tickets",
    operation_id="app.access.ticket.create",
    include_in_schema=False,
)
async def create_app_access_ticket(
    request: Request, user: CurrentUser, use_cases: AppUseCasesDep
) -> Response:
    """A one-minute ticket for the app host this request comes from.

    The app is named by the browser's ``Origin`` and nothing else, so one app's
    page cannot obtain a ticket for another. Every refusal -- a foreign origin,
    a delegated token, a missing app, a forbidden one -- is the same 404, so
    the answer does not confirm that a guessed slug exists.
    """
    origin = request.headers.get("origin", "")
    host = private_app_host(urlsplit(origin).netloc)
    session = getattr(request.state, "session", None)
    if (
        host is None
        or host.origin != origin
        or session is None
        or getattr(request.state, "delegation_claims", None) is not None
    ):
        return private_error(AppNotFoundError())
    try:
        app_id = await use_cases.authorize_host_access(
            slug=host.slug,
            release_ref=host.release_ref,
            request=request,
            user_id=user.id,
        )
    except DomainError as error:
        if error.status_code not in {401, 403, 404, 410}:
            raise
        return private_error(AppNotFoundError())
    claims = AppAccessClaims(
        user_id=user.id,
        app_id=app_id,
        origin=origin,
        session_handle=session.get_handle(),
        expires_at=int(time.time()) + TICKET_TTL_SECONDS,
    )
    return JSONResponse(
        {
            "ticket": mint_app_access_token(AppAccessPurpose.TICKET, claims),
            "expires_in_seconds": TICKET_TTL_SECONDS,
        },
        headers=PRIVATE_NO_STORE,
    )


@router.post("/public/apps/_lemma/app-access/redeem", include_in_schema=False)
async def redeem_app_access(request: Request, data: AppAccessRedeemRequest) -> Response:
    """Trade a ticket for this app host's access cookie.

    Reached as ``/_lemma/app-access/redeem`` on the app host, which host routing
    maps here. The ticket must have been issued to this exact origin, and the
    request must come from a page on it.
    """
    host = private_app_host(request.headers.get("host", ""))
    claims = (
        verify_app_access_token(
            data.ticket, purpose=AppAccessPurpose.TICKET, origin=host.origin
        )
        if host is not None and request.headers.get("origin") == host.origin
        else None
    )
    if claims is None:
        return private_error(AppAccessInvalidError())
    cookie = replace(claims, expires_at=int(time.time()) + COOKIE_TTL_SECONDS)
    response = JSONResponse(
        {"expires_in_seconds": COOKIE_TTL_SECONDS}, headers=PRIVATE_NO_STORE
    )
    response.set_cookie(
        ACCESS_COOKIE,
        mint_app_access_token(AppAccessPurpose.COOKIE, cookie),
        max_age=COOKIE_TTL_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response
