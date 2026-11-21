"""What both app access routes and the app host share: names, host, refusals."""

from typing import NamedTuple
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.modules.apps.api.host_routing import app_label_from_host, split_release_label
from app.modules.apps.config import apps_settings
from app.modules.apps.domain.entities import public_app_url

ACCESS_COOKIE = "__Host-lemmaAppAccess"
# The same cookie on a local stack served over plain HTTP. A `__Host-` cookie
# must be Secure, and a browser need not accept Secure from an http:// page --
# Chromium does on `*.localhost`, which is not a promise the others make.
# Host-only all the same: no Domain, set and read only by the one app host, and
# its claims are bound to that host's origin.
LOCAL_ACCESS_COOKIE = "lemmaAppAccess"
PRIVATE_NO_STORE = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"}


class AppHost(NamedTuple):
    origin: str
    slug: str
    release_ref: str | None
    secure: bool = True

    @property
    def cookie(self) -> str:
        return ACCESS_COOKIE if self.secure else LOCAL_ACCESS_COOKIE


def private_app_host(netloc: str) -> AppHost | None:
    """The app host at ``netloc``, if private apps may be opened there.

    Over HTTPS: the access cookie is ``__Host-`` and so ``Secure``. Over plain
    HTTP only where a local stack opted in (``_opens_over_local_http``), with a
    cookie of its own. That leaves the desktop's ``*.localhost`` hosts on their
    own routing and auth. The host must be exactly the app's canonical one --
    not a deeper subdomain, not another port -- since the cookie, and the
    ticket, are bound to it.
    """
    secure = settings.api_url.startswith("https://")
    label = app_label_from_host(netloc)
    origin = public_app_url(label) if label else None
    if origin is None or urlsplit(origin).netloc != netloc.lower():
        return None
    if not secure and not _opens_over_local_http(origin):
        return None
    slug, release_ref = split_release_label(label or "")
    if slug is None:
        return None
    return AppHost(origin, slug, release_ref, secure)


def _opens_over_local_http(origin: str) -> bool:
    """A `make dev` stack: opted in, local, and an app host the browser itself
    resolves to this machine.

    ``.localhost`` names never leave the machine (RFC 6761, and browsers
    hard-wire them to loopback), so a cookie without ``Secure`` crosses no
    network. The setting is refused outside local/testing at startup; it is
    asked again here so a test or a patched setting cannot widen it.
    """
    parts = urlsplit(origin)
    return (
        apps_settings.private_apps_over_http
        and settings.is_local_mode()
        and parts.scheme == "http"
        and (parts.hostname or "").endswith(".localhost")
    )


def private_error(error: DomainError) -> JSONResponse:
    return JSONResponse(
        {"message": error.message, "code": error.code},
        status_code=error.status_code,
        headers=PRIVATE_NO_STORE,
    )
