"""Seeing and undoing what your browser is signed in to.

A credential store nobody can inspect is one nobody can trust. These are the
routes that make that inspectable -- with the difference that there is no
store any more. The sandbox's browser keeps its own profile, so this asks the
browser and reports what it says, every time.

Three consequences worth naming, because they are what changed:

**Nothing here returns a secret**, which used to be a promise kept by leaving
the field out of a response model. It is now kept by the value never crossing
the sandbox boundary at all -- `browser_relay/cookies.py` reads hosts and
expiries and nothing else.

**Delete really signs you out.** The previous version removed Lemma's
encrypted copy and left the browser as it was, which is why the UI had to say
"this does not sign you out at the site". It does now.

**Listing does not wake anything.** A paused sandbox answers `sleeping`, the
same shape the files routes use, rather than starting a computer because
somebody opened a settings page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser
from app.core.log.log import get_logger
from app.modules.web_login.services.origin import InvalidOrigin, normalize_origin
from app.modules.web_login.services.sites import (
    same_site,
    site_from_origin,
    site_of,
)
from app.modules.workspace.contracts.browser import ProfileCookie, ProfileCookies
from sandbox_runtime.errors import SandboxCapabilityUnsupported

router = APIRouter(prefix="/web-logins", tags=["Web Logins"])

logger = get_logger(__name__)


class WebLoginResponse(BaseModel):
    site: str = Field(
        description=(
            "The site, as a person would name it. Cookies are grouped by "
            "registrable domain, so `asur.work` and `api.asur.work` are one "
            "login rather than two -- the second being the half nobody "
            "visited on purpose."
        )
    )
    cookie_count: int = Field(
        description="How many cookies this site has. A rough sense of scale."
    )
    expires: datetime | None = Field(
        default=None,
        description=(
            "When the soonest of them lapses, which is the closest thing to "
            "'when will I have to sign in again'. Null when they are all "
            "session cookies, which go when the browser does."
        ),
    )
    signed_in: bool = Field(
        default=False,
        description=(
            "True when somebody answered 'yes, I signed in' to a sign-in "
            "request for this site. The cookies cannot say this on their "
            "own: a real profile held two session cookies for a site that "
            "was signed in and six for one that merely had a video played "
            "on it, identical on every flag. False means only 'nobody said "
            "so' -- the browser may still have a usable session."
        ),
    )


class WebLoginListResponse(BaseModel):
    items: list[WebLoginResponse]
    sleeping: bool = Field(
        default=False,
        description=(
            "True when the computer is paused and was not woken to answer. "
            "Items are empty; its browser still holds whatever it held."
        ),
    )


class ForgetResponse(BaseModel):
    site: str
    forgotten: bool = Field(
        description="False when the browser was holding nothing for this site."
    )


class BrowserView(Protocol):
    """The two things these routes ask a browser for.

    Named, rather than left as whatever `browser_view_service()` returns, so
    that the routes can take it as a dependency. They used to call a
    module-level factory, which meant a test could only reach them by
    patching that factory -- a double placed inside the unit under test,
    which certifies the half you did not write and survives a rename that
    should have failed.
    """

    async def signed_in_sites(
        self, user_id: UUID, *, wake: bool = False
    ) -> ProfileCookies:
        """Every cookie host the browser holds, and whether it is running."""
        ...

    async def forget_sites(
        self, user_id: UUID, *, domains: list[str], sites: list[str]
    ) -> int:
        """Clear these hosts, for these sites. Returns cookies dropped."""
        ...


def _browser() -> BrowserView:
    """The browser service, imported at call time.

    Naming it at module scope pulls the whole provider stack -- Docker, the
    E2B SDK -- into the import graph of every process that merely registers
    these routes.
    """
    from app.modules.workspace.contracts.browser import browser_view_service

    return browser_view_service()()


#: The live browser, or whatever a caller passes instead.
Browser = Annotated[BrowserView, Depends(_browser)]


def _as_sites(
    cookies: list[ProfileCookie], signed_in: list[str] | None = None
) -> list[WebLoginResponse]:
    """Group raw cookie hosts into the sites a person would recognise.

    Here rather than in the sandbox because this is the public-suffix
    question, and the suffix list lives on this side. The relay reports hosts
    and deletes the hosts it is given; it does not know that two of them are
    one login.

    `signed_in` is intersected rather than trusted outright: it is a file of
    names beside the profile, so a mark left behind by a profile somebody
    deleted by hand describes nothing. A site with no cookies never reaches
    this list, which is what makes the stale case correct itself.
    """
    marked = {site.lower() for site in signed_in or []}
    grouped: dict[str, list[float | None]] = {}
    for cookie in cookies:
        host = str(cookie.get("domain") or "")
        if not host:
            continue
        # A host with no registrable domain -- `localhost`, a bare IP -- is
        # its own site. Grouping those under "" would collapse every one of
        # them into a single meaningless row.
        key = site_of(host) or host
        expires = cookie.get("expires")
        grouped.setdefault(key, []).append(
            float(expires) if isinstance(expires, (int, float)) else None
        )
    items = []
    for site, expiries in sorted(grouped.items()):
        real = [e for e in expiries if e]
        items.append(
            WebLoginResponse(
                site=site,
                cookie_count=len(expiries),
                expires=(
                    datetime.fromtimestamp(min(real), tz=timezone.utc) if real else None
                ),
                signed_in=site.lower() in marked,
            )
        )
    return items


@router.get(
    "",
    response_model=WebLoginListResponse,
    operation_id="web_login.list",
    summary="List the sites your browser is signed in to",
)
async def list_web_logins(
    current_user: CurrentUser,
    browser: Browser,
    wake: bool = Query(
        default=False,
        description=(
            "Start the computer if it is paused. Off by default so that "
            "rendering this list is never what wakes one."
        ),
    ),
) -> WebLoginListResponse:
    try:
        answer = await browser.signed_in_sites(current_user.id, wake=wake)
    except SandboxCapabilityUnsupported:
        # A fabric with no reachable browser. Not an error to a reader: there
        # is nothing signed in because there is nowhere to be signed in.
        logger.warning("web_login.list.no_browser_capability.degraded")
        return WebLoginListResponse(items=[])
    except _relay_unavailable() as exc:
        # Logged, because "sleeping" is also what a *broken* relay looks like
        # from here and the two are indistinguishable to the reader. Without
        # this line the answer is the same sentence whether the computer is
        # paused or the relay is failing to start, and only one of those is
        # somebody's own doing.
        logger.warning(
            "web_login.list.relay_unavailable.degraded",
            error_type=type(exc).__name__,
            detail=str(exc)[:200],
            wake=wake,
        )
        return WebLoginListResponse(items=[], sleeping=True)
    if not answer["running"]:
        return WebLoginListResponse(items=[], sleeping=True)
    return WebLoginListResponse(
        items=_as_sites(answer["cookies"], answer.get("signed_in") or [])
    )


@router.delete(
    "",
    response_model=ForgetResponse,
    operation_id="web_login.delete",
    summary="Sign your browser out of a site",
)
async def forget_web_login(
    current_user: CurrentUser,
    browser: Browser,
    origin: str = Query(description="The site to forget, as an origin or a host."),
) -> ForgetResponse:
    try:
        site = site_from_origin(normalize_origin(origin))
    except InvalidOrigin as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    try:
        answer = await browser.signed_in_sites(current_user.id)
    except (SandboxCapabilityUnsupported, _relay_unavailable()) as exc:
        # Refusing rather than reporting success: a person pressing "forget"
        # on a sleeping computer has not had their browser signed out, and
        # telling them otherwise is the failure this whole change exists to
        # stop.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This computer is not running, so its browser cannot be changed yet.",
        ) from exc

    if not answer["running"]:
        # Awake sandbox, retired browser -- no exception, and until now a
        # 200 with `forgotten: false`. The screen read that as "signed out"
        # while the session sat untouched on the durable disk, which is the
        # same lie in a different costume from the one this feature replaced.
        # Cookies are read and cleared over CDP, so a browser that is not
        # running cannot be changed and must not be reported as changed.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The browser is not running, so its cookies cannot be "
                "cleared yet. Open it and try again."
            ),
        )

    hosts = [
        cookie["domain"]
        for cookie in answer["cookies"]
        if same_site(cookie["domain"], site)
    ]
    if not hosts:
        return ForgetResponse(site=site, forgotten=False)
    dropped = await browser.forget_sites(
        current_user.id, domains=sorted(set(hosts)), sites=[site]
    )
    return ForgetResponse(site=site, forgotten=dropped > 0)


def _relay_unavailable() -> type[Exception]:
    from app.modules.workspace.contracts.browser import browser_unavailable

    return browser_unavailable()
