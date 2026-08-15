"""One ``app.session_started`` per person per app session.

Serving a published app's assets is unauthenticated by design -- that route has
no session and never will -- so a "session" cannot be observed where the page is
handed over. It can be observed on the app's *first authenticated API call*,
which is the moment the app actually does something on someone's behalf, and is
a better definition anyway: a bot fetching index.html is not a session.

Two things make it countable, and both are declared by the caller:

* ``X-Lemma-Client: lemma-app/<version>`` resolves the request to the ``APP``
  origin (``app/core/origin.py``);
* ``X-Lemma-App: <uuid>`` says which app.

Both are caller-supplied, so they name a dimension and grant nothing. The app id
is required to parse as a UUID and is otherwise ignored, which bounds what can
reach the analytics store to exactly what the catalog allows.

Deduped on ``(app_id, session handle)`` in Redis with a TTL, so a person
refreshing an app all afternoon is one session, and the key expires on its own
rather than accumulating forever.
"""

from __future__ import annotations

from uuid import UUID

from app.core.analytics import AnalyticsActor, emit
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind, current_origin

logger = get_logger(__name__)

APP_HEADER = "X-Lemma-App"

#: Long enough that an afternoon of use is one session, short enough that the
#: keyspace is bounded by active use rather than by all use ever.
_SESSION_TTL_SECONDS = 12 * 60 * 60

_KEY = "analytics:app-session:{app_id}:{handle}"


def _app_id(connection) -> UUID | None:
    raw = connection.headers.get(APP_HEADER)
    if not raw:
        return None
    try:
        return UUID(raw.strip())
    except (TypeError, ValueError):
        # A caller-supplied header that is not an id is not a dimension value.
        return None


async def maybe_record_app_session(connection, session, user_id: UUID | str) -> None:
    """Record this app session if it is the first request of one.

    Called from the auth path, so it must be cheap for every request that is not
    an app: the header check short-circuits before anything touches Redis.
    """
    app_id = _app_id(connection)
    if app_id is None:
        return

    origin = current_origin()
    if origin is None or origin.kind is not OriginKind.APP:
        # The app header without the app client is somebody else's request
        # carrying a header they should not have. Not an error, not a session.
        return

    handle = getattr(session, "get_handle", lambda: None)()
    if not handle:
        return

    from app.core.infrastructure.redis.client import get_redis

    try:
        redis = get_redis()
        # SET NX: the first request of a session claims it, the rest are no-ops.
        claimed = await redis.set(
            _KEY.format(app_id=app_id, handle=handle),
            "1",
            ex=_SESSION_TTL_SECONDS,
            nx=True,
        )
    except Exception:  # noqa: BLE001 - analytics must never fail a request
        logger.debug("analytics.app_session.cache_unavailable")
        return

    if not claimed:
        return

    # Once per session, so the read is negligible -- and without it the event
    # loses its pod group, which for a pod-scoped noun is most of its value.
    pod_id = await _pod_of(app_id)
    if pod_id is None:
        return
    emit(
        "app.session_started",
        actor=AnalyticsActor.user(user_id),
        origin=Origin(OriginKind.APP),
        pod_id=pod_id,
        properties={"app_id": app_id, "pod_id": pod_id},
    )


async def _pod_of(app_id: UUID) -> UUID | None:
    from sqlalchemy import text

    from app.core.infrastructure.db.session import async_session_maker

    async with async_session_maker() as session:
        return await session.scalar(
            text("SELECT pod_id FROM apps WHERE id = :app_id"), {"app_id": app_id}
        )
