"""What both app access routes and the app host share: names, host, refusals."""

from typing import NamedTuple
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.modules.apps.api.host_routing import app_label_from_host, split_release_label
from app.modules.apps.domain.entities import public_app_url

ACCESS_COOKIE = "__Host-lemmaAppAccess"
PRIVATE_NO_STORE = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}


class AppHost(NamedTuple):
    origin: str
    slug: str
    release_ref: str | None


def private_app_host(netloc: str) -> AppHost | None:
    """The app host at ``netloc``, if private apps may be opened there.

    Only over HTTPS: the access cookie is ``__Host-`` and so ``Secure``. That
    leaves the desktop's ``*.localhost`` hosts on their own routing and auth.
    The host must be exactly the app's canonical one -- not a deeper subdomain,
    not another port -- since the cookie, and the ticket, are bound to it.
    """
    if not settings.api_url.startswith("https://"):
        return None
    label = app_label_from_host(netloc)
    origin = public_app_url(label) if label else None
    if origin is None or urlsplit(origin).netloc != netloc.lower():
        return None
    slug, release_ref = split_release_label(label or "")
    if slug is None:
        return None
    return AppHost(origin, slug, release_ref)


def private_error(error: DomainError) -> JSONResponse:
    return JSONResponse(
        {"message": error.message, "code": error.code},
        status_code=error.status_code,
        headers=PRIVATE_NO_STORE,
    )
