"""A pod app's page acts as its viewer, and only in its own pod (H20).

An app's page runs on ``<slug>.<app_base_domain>``, same-site with the API, and
calls it with the viewer's session cookie. On main that call *was* the viewer
everywhere: an editor's app, opened by an organization owner, could change the
organization's settings, its members, its billing, and every other pod. It is
still the viewer -- with their whole access inside the app's own pod, so nothing
an app does there changes -- but a route outside that pod is refused unless
``actor_policy.APP_ROUTES`` opens it, and a ``{pod_id}`` or ``{organization_id}``
in the route must be the app's own.

**How the app is known.** By the host the browser says the request came from,
which the page's script cannot choose:

- ``Origin`` -- the app calling the API on the API's host. The browser sets it
  on every CORS request and every non-GET request, and a script cannot remove
  or change it.
- ``Host`` -- the app calling the API through its own origin's ``/_lemma``
  door (``app_api_via_app_origin``), where host routing keeps the Host it was
  addressed to.

Recognition only ever *narrows* a session, so a client that sends an app's
Origin gains nothing by it. Only a cookie session is recognised: a bearer token
(the SDK, the CLI, MCP) is something its holder was handed, not something a
page's script picks up by being opened.

**Fail closed.**

- ``Origin: null`` is what an opaque document sends -- a sandboxed frame, a
  ``data:`` page. It names no host at all, so a cookie request carrying it is
  refused on every state-changing method rather than read by its ``Host``.
- An app host whose slug names no app is refused rather than read as the
  viewer: a page left open on a deleted app keeps nothing.

What a script on an app's page can still send without an ``Origin`` is a
no-CORS GET: a navigation, an image. Its response is opaque to the script, and
GET routes change nothing. The API's own host serving an app's HTML would be
the other way round, so it serves it sandboxed (``apps/api/asset_response``).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from starlette.requests import HTTPConnection

from app.core.api.actor_policy import (
    APP_ROUTE_NOT_ALLOWED,
    app_may_call,
    refuse_app_route,
    route_template,
)
from app.core.app_hosts import app_label_from_host, split_release_label
from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.cache.resilient_cache import ResilientJsonCache
from app.core.infrastructure.db.session import async_session_maker
from app.core.log.log import get_logger

#: How long a slug's app is remembered. A slug renamed away and taken by another
#: app inside this window is still pinned to the first app's pod for that long.
APP_HOST_CACHE_TTL_SECONDS = 30

#: The code an app host that names no app is refused with.
APP_HOST_UNKNOWN = "APP_HOST_UNKNOWN"
#: The code a cookie request from an opaque origin is refused with.
ORIGIN_NULL_NOT_ALLOWED = "ORIGIN_NULL_NOT_ALLOWED"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class UnknownAppHost(Exception):
    """The request came from an app host whose slug names no app."""


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AppOnHost:
    """The pod app a request's page belongs to."""

    app_id: UUID
    pod_id: UUID
    organization_id: UUID
    name: str


def is_null_origin(origin: str | None) -> bool:
    return origin is not None and origin.strip().lower() == "null"


def app_host_slug(connection: HTTPConnection) -> str | None:
    """The slug of the app host this request came from, or None.

    ``Origin`` first: a page on an app host calling the API's own host. Then
    ``Host``: the same page calling through its own origin's ``/_lemma`` door.
    A preview host (``orders--r7``) is the same app as its live one. A null
    Origin is the caller's to refuse; it is not read as "no Origin".
    """
    origin = connection.headers.get("origin")
    label = None
    if origin and not is_null_origin(origin):
        try:
            label = app_label_from_host(urlsplit(origin).netloc)
        except ValueError:
            label = None
    if label is None:
        label = app_label_from_host(connection.headers.get("host") or "")
    if label is None:
        return None
    slug, _release = split_release_label(label)
    return slug


_cache: ResilientJsonCache | None = None


def _get_cache() -> ResilientJsonCache:
    global _cache
    if _cache is None:
        _cache = ResilientJsonCache(
            name="app_host_pin_cache",
            key_prefix="apps:host-pin",
            redis_url=settings.redis_url,
            ttl_seconds=APP_HOST_CACHE_TTL_SECONDS,
        )
    return _cache


def _encode(app: AppOnHost) -> str:
    # Three ids and a name, joined: a name holds no newline (normalized at
    # create), and this runs on every app request, so no serializer is needed.
    return f"{app.app_id}\n{app.pod_id}\n{app.organization_id}\n{app.name}"


def _decode(payload: str) -> AppOnHost:
    app_id, pod_id, organization_id, name = payload.split("\n", 3)
    return AppOnHost(
        app_id=UUID(app_id),
        pod_id=UUID(pod_id),
        organization_id=UUID(organization_id),
        name=name,
    )


async def forget_app_host(slug: str | None) -> None:
    """Drop what the cache holds for ``slug``.

    For an app whose slug changed or that was deleted: otherwise its old slug
    keeps resolving to it -- or, once another app takes the slug, to the wrong
    app and pod -- for up to ``APP_HOST_CACHE_TTL_SECONDS``.
    """
    if slug:
        await _get_cache().delete(slug)


def forget_app_host_after_commit(holder: object, slug: str | None) -> None:
    """``forget_app_host`` once the unit of work ``holder`` writes through (it,
    or its ``uow``) commits: a read before the commit would put it back."""
    uow = getattr(holder, "uow", holder)
    after_commit = getattr(uow, "after_commit", None)
    if slug and after_commit is not None:
        after_commit(lambda: forget_app_host(slug))


async def app_on_host(slug: str) -> AppOnHost | None:
    """The app served at ``slug``. Read by SQL rather than through the apps
    module, which core may not import -- the analytics app-session hook reads
    the same table the same way."""
    cache = _get_cache()
    cached = await cache.get(slug, _decode)
    if cached is not None:
        return cached
    async with async_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT apps.id, apps.pod_id, pods.organization_id, apps.name "
                    "FROM apps JOIN pods ON pods.id = apps.pod_id "
                    "WHERE apps.public_slug = :slug"
                ),
                {"slug": slug},
            )
        ).first()
    if row is None:
        return None
    found = AppOnHost(
        app_id=row.id,
        pod_id=row.pod_id,
        organization_id=row.organization_id,
        name=row.name,
    )
    await cache.set(slug, _encode(found))
    return found


async def app_of_connection(connection: HTTPConnection) -> AppOnHost | None:
    """The app whose page this request or socket comes from, or None.

    Raises :class:`UnknownAppHost` when it comes from an app host that names
    no app.
    """
    slug = app_host_slug(connection)
    if slug is None:
        return None
    app = await app_on_host(slug)
    if app is None:
        raise UnknownAppHost(slug)
    return app


def app_request_of(connection: HTTPConnection) -> AppOnHost | None:
    """The app a request was recognised as coming from, once ``verify_auth``
    has run; None for the person themselves."""
    return getattr(connection.state, "app_request", None)


def refuse_other_pod(connection: HTTPConnection, named: UUID | None) -> bool:
    """Refuse an app's page naming a pod other than its own in a body or query.

    The pin reads only path parameters; a route outside ``/pods/{pod_id}`` that
    takes its pod from what the caller sends calls this with it. For a person
    it does nothing. Under the same switch as a path's pod: until
    ``app_pin_cross_pod_enforced`` is on, another pod is recorded and served,
    and True says so.
    """
    app = app_request_of(connection)
    if app is None or named is None or named == app.pod_id:
        return False
    method = str(connection.scope.get("method", "GET")).upper()
    template = route_template(connection)
    if not settings.app_pin_cross_pod_enforced:
        logger.warning(
            "apps.pin.cross_pod_observed",
            app_id=str(app.app_id),
            app_pod_id=str(app.pod_id),
            route=f"{method} {template}",
        )
        return True
    raise DomainError(
        "A pod app's page acts only in its own pod, and this request names another.",
        code=APP_ROUTE_NOT_ALLOWED,
        status_code=403,
        details={"method": method, "route": template},
    )


def app_pod_for(connection: HTTPConnection, named: UUID | None) -> UUID | None:
    """The pod a route outside ``/pods/{pod_id}`` acts in, given the one the
    request names: as named for a person; for an app's page, its own pod,
    whether or not it names one (:func:`refuse_other_pod`) -- or, while the
    cross-pod refusal is off, the other pod it named."""
    if refuse_other_pod(connection, named):
        return named
    app = app_request_of(connection)
    return named if app is None else app.pod_id


#: What an app's page may invite someone as: a member of the organization,
#: and at most a user of the app's own pod.
_APP_INVITABLE_POD_ROLES = frozenset({None, "POD_VIEWER", "POD_USER"})


def refuse_app_invitation_above_member(
    connection: HTTPConnection,
    *,
    role: str,
    pod_id: UUID | None,
    pod_role: str | None,
) -> None:
    """An app invites as a plain member, never into a higher role.

    Inviting is the one change to the organization's people an app may make
    (``APP_ROUTES``). Anything more -- an owner or editor, a pod admin, another
    pod -- is what an editor's app, opened by an owner, would use to promote
    the editor, so it is the viewer's to do in Lemma itself.
    """
    app = app_request_of(connection)
    if app is None:
        return
    within = (
        role == "ORG_MEMBER"
        and (pod_id is None or pod_id == app.pod_id)
        and pod_role in _APP_INVITABLE_POD_ROLES
    )
    if not within:
        raise DomainError(
            "An app can invite someone only as a member of the organization "
            "(and at most a user of its own pod). Invite them into a higher "
            "role from Lemma.",
            code=APP_ROUTE_NOT_ALLOWED,
            status_code=403,
        )


def _same_id(raw: object, expected: UUID) -> bool:
    try:
        return UUID(str(raw)) == expected
    except ValueError:
        return False


def _require_app_route(connection: HTTPConnection, app: AppOnHost) -> None:
    method = str(connection.scope.get("method", "GET")).upper()
    template = route_template(connection)
    if template is None or not app_may_call(method, template):
        raise refuse_app_route(method, template)
    params = connection.path_params
    if "pod_id" in params and not _same_id(params["pod_id"], app.pod_id):
        if settings.app_pin_cross_pod_enforced:
            raise refuse_app_route(method, template)
        # Served as on main for now, and recorded: an app that relies on
        # another pod shows up here before the refusal is switched on.
        logger.warning(
            "apps.pin.cross_pod_observed",
            app_id=str(app.app_id),
            app_pod_id=str(app.pod_id),
            route=f"{method} {template}",
        )
    if "organization_id" in params and not _same_id(
        params["organization_id"], app.organization_id
    ):
        raise refuse_app_route(method, template)


async def pin_app_request(connection: HTTPConnection) -> None:
    """Pin a person's cookie request from an app's page to that app's pod.

    Called by ``verify_auth`` for a person's own cookie session, after the
    route is matched. Raises a typed 403 :class:`DomainError` for a refusal.
    """
    method = str(connection.scope.get("method", "GET")).upper()
    if method not in _SAFE_METHODS and is_null_origin(connection.headers.get("origin")):
        raise DomainError(
            "A signed-in request from an opaque origin (Origin: null) may not "
            "change anything.",
            code=ORIGIN_NULL_NOT_ALLOWED,
            status_code=403,
        )
    try:
        app = await app_of_connection(connection)
    except UnknownAppHost as unknown:
        raise DomainError(
            "This request comes from the host of an app that does not exist.",
            code=APP_HOST_UNKNOWN,
            status_code=403,
        ) from unknown
    if app is None:
        return
    connection.state.app_request = app
    _require_app_route(connection, app)


__all__ = [
    "APP_HOST_UNKNOWN",
    "ORIGIN_NULL_NOT_ALLOWED",
    "AppOnHost",
    "UnknownAppHost",
    "app_host_slug",
    "app_of_connection",
    "app_on_host",
    "app_pod_for",
    "app_request_of",
    "is_null_origin",
    "pin_app_request",
    "refuse_other_pod",
]
