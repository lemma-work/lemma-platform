"""Background moves of existing file rows onto the new storage model.

Two backfills, each a chain of small ``admin`` items that page by id and enqueue
their own continuation, armed by a periodic that is a cheap no-op once there is
nothing left -- never a boot hook, which would grow with the table:

* ``datastore.parent_backfill`` fills ``parent_id`` for rows written before it
  existed (new writes set it themselves).
* ``datastore.storage_copy`` copies a path-layout file's original and derived
  container to its object-layout keys and flips the row. It runs only while
  ``DATASTORE_OBJECT_STORAGE_LAYOUT`` is on. Reads look in both layouts
  throughout, so a row flipped between a reader's read and its download still
  resolves.

The old keys are not dropped with the flip. Signed URLs name an object key:
the HMAC token served by ``/public/datastore/files`` embeds it, and on GCS the
URL is the bucket's own, signed for that key, which nothing of ours sees when
it is opened. A URL minted before the copy -- a page image in an agent's
transcript, say -- names the path key, and dropping it would 404 every one of
them. Resolving a link by file id instead would need the id in every URL and
could never reach a GCS signed URL at all, so the keys simply outlive every
link that could name them: the flip enqueues ``datastore.storage_release``,
due once the longest URL anyone could have minted has expired, which drops
them -- unless a path-layout file has since been written at that path (the
switch turned back off), whose bytes those keys now are.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text

from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    FastContext,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import (
    DatastoreDomainError,
    DatastoreObjectNotFoundError,
)
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.infrastructure.storage_paths import (
    object_file_prefix,
    object_keys,
    path_keys,
)

logger = get_logger(__name__)

PARENT_BACKFILL = "datastore.parent_backfill"
STORAGE_COPY = "datastore.storage_copy"
STORAGE_RELEASE = "datastore.storage_release"
PARENT_BACKFILL_PAGE = 1_000
STORAGE_COPY_PAGE = 100
BACKFILL_EVERY = timedelta(hours=1)

# Rows below the root with no parent recorded, a page of them by id, each
# matched to the folder row at its parent path. A row whose parent folder has
# no row (written before folders were materialised) stays NULL; the cursor
# moves past it, so it is visited once per pass rather than forever.
_PARENT_PAGE = text(
    """
    WITH page AS (
        SELECT id, pod_id,
               left(path, length(path) - strpos(reverse(path), '/')) AS parent_path
        FROM datastore_files
        WHERE parent_id IS NULL
          AND strpos(substr(path, 2), '/') > 0
          AND id > :after
        ORDER BY id
        LIMIT :limit
    ), linked AS (
        UPDATE datastore_files AS child
        SET parent_id = parent.id
        FROM page
        JOIN datastore_files AS parent
          ON parent.pod_id = page.pod_id AND parent.path = page.parent_path
        WHERE child.id = page.id
        RETURNING child.id
    )
    SELECT (SELECT id FROM page ORDER BY id DESC LIMIT 1) AS last_id,
           (SELECT count(*) FROM page) AS seen,
           (SELECT count(*) FROM linked) AS linked
    """
)

_COPY_PAGE = text(
    """
    SELECT id, pod_id, path, content_sha256, indexed_sha256
    FROM datastore_files
    WHERE storage_layout = 'path' AND kind = 'FILE' AND id > :after
    ORDER BY id
    LIMIT :limit
    """
)

_FLIP = text(
    """
    UPDATE datastore_files SET storage_layout = 'object'
    WHERE id = :id AND storage_layout = 'path' AND path = :path
      AND content_sha256 IS NOT DISTINCT FROM :sha
    """
)

# Whether a path-layout file now sits at the released path: its bytes are at
# the keys the release would drop.
_PATH_TAKEN = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM datastore_files
        WHERE pod_id = :pod_id AND path = :path
          AND storage_layout = 'path' AND kind = 'FILE'
    )
    """
)

#: Past the ceiling on a signed URL's lifetime: a GCS URL is signed for whole
#: hours, rounded, so it can outlive the seconds asked for by up to half of one.
_RELEASE_MARGIN = timedelta(hours=1)

_ZERO_ID = UUID(int=0)


def release_delay() -> timedelta:
    """How long old keys are kept after a copy: every URL naming them is dead."""
    return (
        timedelta(seconds=datastore_settings.datastore_signed_url_max_expiry_seconds)
        + _RELEASE_MARGIN
    )


class CursorPayload(BaseModel):
    after: UUID = _ZERO_ID


class ReleasePayload(BaseModel):
    pod_id: UUID
    file_id: UUID
    path: str


async def run_parent_backfill(ctx: FastContext, payload: CursorPayload) -> None:
    row = (
        await ctx.uow.session.execute(
            _PARENT_PAGE, {"after": payload.after, "limit": PARENT_BACKFILL_PAGE}
        )
    ).one()
    if row.seen == PARENT_BACKFILL_PAGE and row.last_id is not None:
        await ctx.work.add(
            PARENT_BACKFILL,
            SYSTEM_NAMESPACE,
            CursorPayload(after=row.last_id),
            dedup_key=f"{PARENT_BACKFILL}:{row.last_id}",
        )
    if row.linked:
        logger.info("datastore.parent_backfill.page.observed", linked=int(row.linked))


async def _copy_one(
    storage: DatastoreStoragePort,
    *,
    pod_id: UUID,
    file_id: UUID,
    path: str,
    sha: str | None,
    indexed_sha: str | None,
) -> bool:
    """Copy one file's bytes across; False when its original is missing here."""
    legacy = path_keys(pod_id, path)
    target = object_keys(pod_id, file_id, sha)
    try:
        await storage.copy_file(legacy.original, target.original)
    except DatastoreObjectNotFoundError:
        return False
    derived = object_keys(pod_id, file_id, indexed_sha or sha)
    try:
        await storage.copy_prefix(legacy.child_prefix, derived.child_prefix)
    except DatastoreDomainError:
        # Derived artifacts are regenerable; a file that lost them is no worse
        # off than one never converted, and the original is what matters.
        logger.warning(
            "datastore.storage_copy.derived_copy.degraded",
            file_id=str(file_id),
            exc_info=True,
        )
    return True


async def run_storage_copy(ctx: JobContext, payload: CursorPayload) -> None:
    if not datastore_settings.datastore_object_storage_layout:
        return
    async with ctx.checkpoint() as uow:
        rows = (
            await uow.session.execute(
                _COPY_PAGE, {"after": payload.after, "limit": STORAGE_COPY_PAGE}
            )
        ).all()
    storage = create_datastore_storage()
    moved = 0
    for row in rows:
        ctx.raise_if_stopping()
        copied = await _copy_one(
            storage,
            pod_id=row.pod_id,
            file_id=row.id,
            path=row.path,
            sha=row.content_sha256,
            indexed_sha=row.indexed_sha256,
        )
        if not copied:
            continue
        async with ctx.checkpoint() as uow:
            flipped = (
                await uow.session.execute(
                    _FLIP, {"id": row.id, "path": row.path, "sha": row.content_sha256}
                )
            ).rowcount
            if flipped:
                # In the flip's transaction: a flip whose release was lost
                # would keep its old keys forever.
                await ctx.writer(uow).add(
                    STORAGE_RELEASE,
                    SYSTEM_NAMESPACE,
                    ReleasePayload(pod_id=row.pod_id, file_id=row.id, path=row.path),
                    dedup_key=f"{STORAGE_RELEASE}:{row.id}",
                    delay=release_delay(),
                )
        if flipped:
            moved += 1
        else:
            # The row moved or changed while its bytes were copied; the next
            # pass copies it again from where it is now.
            try:
                await storage.delete_prefix(object_file_prefix(row.pod_id, row.id))
            except DatastoreDomainError:
                logger.debug(
                    "datastore.storage_copy.abandoned_copy.diagnostic", exc_info=True
                )
    async with ctx.complete_work() as uow:
        if len(rows) == STORAGE_COPY_PAGE:
            last = rows[-1].id
            await ctx.writer(uow).add(
                STORAGE_COPY,
                SYSTEM_NAMESPACE,
                CursorPayload(after=last),
                dedup_key=f"{STORAGE_COPY}:{last}",
            )
    if moved:
        logger.info("datastore.storage_copy.page.observed", moved=moved)


async def run_storage_release(ctx: JobContext, payload: ReleasePayload) -> None:
    """Drop a copied file's path-layout keys, now nothing can name them."""
    async with ctx.checkpoint() as uow:
        taken = await uow.session.scalar(
            _PATH_TAKEN, {"pod_id": payload.pod_id, "path": payload.path}
        )
    if taken:
        return
    storage = create_datastore_storage()
    legacy = path_keys(payload.pod_id, payload.path)
    # Any other failure is the item's: it is retried, and the keys are not
    # left behind unnoticed.
    with suppress(DatastoreObjectNotFoundError):
        await storage.delete_file(legacy.original)
    with suppress(DatastoreObjectNotFoundError):
        await storage.delete_prefix(legacy.child_prefix)


def register_storage_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=PARENT_BACKFILL,
        cls=WorkClass.ADMIN,
        mode=WorkMode.FAST,
        payload=CursorPayload,
    )(run_parent_backfill)
    registry.handler(
        kind=STORAGE_COPY,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=CursorPayload,
    )(run_storage_copy)
    registry.handler(
        kind=STORAGE_RELEASE,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=ReleasePayload,
    )(run_storage_release)
    registry.periodic(PARENT_BACKFILL, BACKFILL_EVERY, PARENT_BACKFILL)
    registry.periodic(STORAGE_COPY, BACKFILL_EVERY, STORAGE_COPY)


register_storage_work(default_registry)
