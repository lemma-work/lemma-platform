"""The live half of a public link: what is at its path now, and may it go out.

A link records a path and an authority — who shared it, and the agent that did
it for them if one did. It does not record bytes. Every fetch looks the path up
again and asks, as that authority, whether it may still be read: so an edited
page is served edited, a deleted one stops resolving, and a person who loses
access to a file stops sharing it with them.

The same question decides what a shared page may carry. A reference it embeds
is served only while the page still embeds it, only while the person who shared
the page may read it, and only when it is no more private than the page — see
``may_travel_with``.

Answers are reused for ``LIVE_CACHE_SECONDS``: a video
player issues dozens of range requests per view, and an anonymous reader should
not be able to turn each into a database round trip. That window is the whole
of how stale a shared page can be.

The route is unauthenticated, so this opens and closes its own unit of work
around the lookup, and the bytes are streamed only after it has closed.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import asdict, dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.authorization.context import Context
from app.core.authorization.factory import create_authorization_data_service
from app.core.concurrency.offload import run_blocking
from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.datastore.api.dependencies import build_file_service
from app.modules.datastore.domain.errors import (
    DatastoreAccessDeniedError,
    DatastoreFileNotFoundError,
    DatastoreNotFoundError,
    DatastoreObjectNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.services.files.embedded_references import (
    MAX_SCANNED_PAGE_BYTES,
    PageKind,
    embedded_references,
    may_travel_with,
    normalize_reference,
    page_kind,
    resolve_reference,
)
from app.modules.datastore.services.files.projection import datastore_storage_key
from app.modules.datastore.services.files.signed_url import SignedUrlClaims

logger = get_logger(__name__)

#: How long a link's live answer — which file is at the path, and whether the
#: person who shared it may still read it — is reused. Bounds how stale a shared
#: page can be after an edit.
LIVE_CACHE_SECONDS = 15

_LIVE_PREFIX = "datastore:signedurl:live"
_REFERENCES_PREFIX = "datastore:signedurl:refs"
# A page's references are keyed by its digest, so they can only go stale by
# being evicted; this only stops abandoned entries accumulating.
_REFERENCES_TTL_SECONDS = 3600

#: What a refusal looks like to the caller: one answer for "gone", "never
#: existed" and "not yours to see", so a reader probing paths through a link
#: learns nothing about which files exist.
_UNAVAILABLE_ERRORS = (
    DatastoreFileNotFoundError,
    DatastoreAccessDeniedError,
    DatastoreNotFoundError,
    DatastoreValidationError,
)

_REFUSAL_STATUSES = frozenset({401, 403, 404})


class SharedFileUnavailable(Exception):
    """Nothing this link may serve is at that path now."""


@dataclass(frozen=True, slots=True)
class LiveFile:
    path: str
    object_key: str
    content_sha256: str | None
    content_type: str
    filename: str
    size_bytes: int
    visibility: str

    @classmethod
    def of(cls, entity: DatastoreFileEntity) -> "LiveFile":
        return cls(
            path=entity.path,
            object_key=datastore_storage_key(entity),
            content_sha256=entity.content_sha256,
            content_type=entity.content_type,
            filename=entity.name,
            size_bytes=max(0, entity.size_bytes or 0),
            visibility=entity.visibility,
        )


async def _authority(
    uow: SqlAlchemyUnitOfWork, claims: SignedUrlClaims
) -> Context | None:
    """The context the minter was held to, rebuilt now. None when nobody vouches.

    A link whose person has been deleted (`created_by_user_id` is nulled on
    delete) and that no workload minted has nobody left to ask, so it serves
    nothing rather than everything.
    """
    authorization = create_authorization_data_service(uow)
    user_id = claims.created_by_user_id
    if claims.minted_by_workload:
        kind, _, raw_id = claims.minted_by_workload.partition(":")
        try:
            principal_id = UUID(raw_id)
        except ValueError:
            return None
        if user_id is None:
            return await authorization.build_workload_context(
                principal_type=kind.upper(),
                principal_id=principal_id,
                pod_id=claims.pod_id,
            )
        return await authorization.build_delegated_workload_context(
            user_id=user_id,
            principal_type=kind.upper(),
            principal_id=principal_id,
            pod_id=claims.pod_id,
        )
    if user_id is None:
        return None
    return await authorization.build_user_context(user_id=user_id, pod_id=claims.pod_id)


async def _read_as_sharer(claims: SignedUrlClaims, path: str) -> LiveFile:
    async with SessionUnitOfWorkFactory(get_session_maker())() as uow:
        ctx = await _authority(uow, claims)
        if ctx is None:
            raise SharedFileUnavailable(path)
        try:
            entity = await build_file_service(uow).resolve_readable_file(
                claims.pod_id, path, ctx
            )
        except _UNAVAILABLE_ERRORS as exc:
            raise SharedFileUnavailable(path) from exc
        except DomainError as exc:
            # The core authorizer refuses with a bare `DomainError` carrying
            # 401/403. Those are this link's "no"; anything else is a fault.
            if exc.status_code not in _REFUSAL_STATUSES:
                raise
            raise SharedFileUnavailable(path) from exc
    if entity.is_folder:
        raise SharedFileUnavailable(path)
    return LiveFile.of(entity)


def _live_key(code: str, path: str) -> str:
    # The path hashed: it can be long, and it is a pod's file name, which has
    # no business sitting readable in a cache key.
    return f"{_LIVE_PREFIX}:{code}:{hashlib.sha256(path.encode()).hexdigest()}"


# One file's metadata either way — a few hundred bytes — so these stay on the
# loop; the page scan below is the part that is not small.
def _encode_live(found: LiveFile | None) -> str:
    return json.dumps(asdict(found) if found else None)


def _decode_live(raw: str) -> LiveFile | None:
    payload = json.loads(raw)
    return LiveFile(**payload) if payload is not None else None


def _scan_page(content: bytes, kind: PageKind) -> tuple[frozenset[str], str]:
    """The page's references, and the same as JSON for the cache — CPU over a
    page of up to ``MAX_SCANNED_PAGE_BYTES``, so it runs off the loop."""
    found = embedded_references(content.decode("utf-8", errors="replace"), kind=kind)
    return found, json.dumps(sorted(found))


def _decode_references(raw: str) -> frozenset[str]:
    return frozenset(json.loads(raw))


async def live_file(code: str, claims: SignedUrlClaims, path: str) -> LiveFile:
    """The file at ``path`` now, as the link's authority may read it.

    Raises ``SharedFileUnavailable`` when there is nothing, or nothing it may
    read. Both answers are reused for the cache window — the negative one so a
    reader asking for a missing picture over and over is not a query each time.
    """
    ttl = LIVE_CACHE_SECONDS
    key = _live_key(code, path)
    redis = get_redis(url=settings.redis_url)
    if ttl > 0:
        try:
            cached = await redis.get(key)
        except RedisError:
            cached = None
        if cached is not None:
            remembered = _decode_live(cached)
            if remembered is None:
                raise SharedFileUnavailable(path)
            return remembered

    try:
        found: LiveFile | None = await _read_as_sharer(claims, path)
    except SharedFileUnavailable:
        found = None

    if ttl > 0:
        try:
            await redis.set(key, _encode_live(found), ex=ttl)
        except RedisError as exc:
            # The answer is still right; only the next fetch pays for it again.
            logger.warning(
                "datastore.signed_url.live_cache_write_failed.observed",
                pod_id=str(claims.pod_id),
                error_type=type(exc).__name__,
            )
    if found is None:
        raise SharedFileUnavailable(path)
    return found


async def _page_references(
    code: str, page: LiveFile, storage: DatastoreStoragePort
) -> frozenset[str]:
    kind = page_kind(page.content_type, page.filename)
    if kind is None or page.size_bytes > MAX_SCANNED_PAGE_BYTES:
        return frozenset()

    redis = get_redis(url=settings.redis_url)
    key = (
        f"{_REFERENCES_PREFIX}:{code}:{page.content_sha256}"
        if page.content_sha256
        else None
    )
    if key is not None:
        try:
            cached = await redis.get(key)
        except RedisError:
            cached = None
        if cached is not None:
            return await run_blocking(_decode_references, cached)

    try:
        content = await storage.download_file(page.object_key)
    except DatastoreObjectNotFoundError:
        return frozenset()
    if len(content) > MAX_SCANNED_PAGE_BYTES:
        return frozenset()
    found, encoded = await run_blocking(_scan_page, content, kind)

    if key is not None:
        # Unwritten, the next fetch scans the page again; nothing is wrong.
        with suppress(RedisError):
            await redis.set(key, encoded, ex=_REFERENCES_TTL_SECONDS)
    return found


async def embedded_file(
    code: str,
    claims: SignedUrlClaims,
    reference: str,
    storage: DatastoreStoragePort,
) -> LiveFile:
    """What the shared page loads at ``reference``, if it may go out with it.

    Every condition is asked of the page as it is now: it must still embed the
    reference, the person who shared it must still read the target, and the
    target must not be more private than the page.
    """
    wanted = normalize_reference(reference)
    if wanted is None:
        raise SharedFileUnavailable(reference)
    page = await live_file(code, claims, claims.path)
    if wanted not in await _page_references(code, page, storage):
        raise SharedFileUnavailable(reference)
    target = resolve_reference(wanted, page_path=page.path)
    if target is None:
        raise SharedFileUnavailable(reference)
    resource = await live_file(code, claims, target)
    if not may_travel_with(
        page_visibility=page.visibility, resource_visibility=resource.visibility
    ):
        raise SharedFileUnavailable(reference)
    return resource
