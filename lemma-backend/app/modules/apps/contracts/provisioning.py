"""What another module does to a pod's apps when it builds one.

Ten operations, not `AppService`. The archive pair keeps the discipline the
service documents and a handed-out service object cannot enforce: resolving an
archive's location is a DB read, reading its bytes is not, and they are separate
calls so the transfer does not hold a pooled connection for its whole length.

The upload trio at the bottom is the same discipline on the write side, and it
is why the last five arrived. `app/composition/pod_bundle_apps.py` forwarded
`build_app_service`, so the bundle importer held an `AppService` across three
phases -- and reused the one it built inside a closed unit of work to do the
storage write, because that is what having the object made possible. The phases
are three calls now, and only two of them take a unit of work.

`write_app_bundle_storage` takes none at all: it is the connectionless half, and
the signature is where that has to be said. It is not a rule the caller can
follow by reading a service's docstring, and the caller was not following it.

A submodule rather than `contracts/__init__`: this reaches the service layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.apps.api.dependencies import (
    _get_app_storage_factory,
    build_app_service,
)
from app.modules.apps.domain.entities import AppEntity
from app.modules.apps.services.app_storage_phase import (
    AppStoragePhase,
    _UploadPlan,
    _WrittenBundle,
)


async def list_app_names(
    uow, *, pod_id: UUID, user_id: UUID, ctx: Context
) -> list[str]:
    """Every app in the pod this reader may see."""
    apps, _ = await build_app_service(uow).list_apps(
        pod_id, user_id, 1000, None, ctx=ctx
    )
    return [str(app.name or "") for app in apps]


async def require_app(
    uow, *, pod_id: UUID, name: str, user_id: UUID, ctx: Context
) -> AppEntity:
    """The named app, raising ``AppNotFoundError`` when the pod has none."""
    return await build_app_service(uow).get_app_by_name(
        pod_id, name, user_id, raise_not_found=True, ctx=ctx
    )


async def resolve_app_source_archive(
    uow, *, pod_id: UUID, name: str, user_id: UUID, ctx: Context
) -> tuple[UUID, str]:
    """Authorize and locate an app's source archive.

    Raises ``AppNotFoundError`` when the app has none. Pair with
    :func:`read_app_archive`, which needs no DB session.
    """
    return await build_app_service(uow).resolve_source_archive(
        pod_id, name, user_id, ctx=ctx
    )


async def resolve_app_dist_archive(
    uow, *, pod_id: UUID, name: str, user_id: UUID, ctx: Context
) -> tuple[UUID, str]:
    """Authorize and locate an app's built ``dist`` archive.

    Raises ``AppNotFoundError`` when the app has none. Pair with
    :func:`read_app_archive`.
    """
    return await build_app_service(uow).resolve_dist_archive(
        pod_id, name, user_id, ctx=ctx
    )


async def read_app_archive(uow, *, app_id: UUID, archive_path: str) -> bytes:
    """An archive's bytes, read from app storage with no DB session held."""
    return await build_app_service(uow).read_archive(app_id, archive_path)


async def find_app_by_name(
    uow, *, pod_id: UUID, name: str, user_id: UUID, ctx: Context
) -> AppEntity | None:
    """The named app, or ``None`` when the pod has none.

    :func:`require_app` for the callers that treat absence as an error. An
    importer does not: absence is the ordinary case on a first import, and a
    caller that has to spell "not found" as a caught exception ends up catching
    a denial with it.
    """
    return await build_app_service(uow).get_app_by_name(
        pod_id, name, user_id, raise_not_found=False, ctx=ctx
    )


async def create_app(uow, *, app: AppEntity, user_id: UUID, ctx: Context) -> AppEntity:
    """Create an app. Raises ``AppConflictError`` when its public slug is taken."""
    return await build_app_service(uow).create_app_with_context(app, user_id, ctx=ctx)


async def resolve_app_bundle_upload(
    uow,
    *,
    pod_id: UUID,
    name: str,
    user_id: UUID,
    has_source: bool,
    dist_archive_bytes: bytes | Path | None,
    ctx: Context,
) -> _UploadPlan:
    """Authorize the upload and decide what has to be written (DB only).

    The plan is opaque: hand it back to :func:`write_app_bundle_storage` and
    :func:`finalize_app_bundle_upload`. Raises ``AppValidationError`` when the
    dist archive is not a servable bundle.
    """
    return await build_app_service(uow).resolve_upload_bundle(
        pod_id,
        name,
        user_id,
        has_source=has_source,
        dist_archive_bytes=dist_archive_bytes,
        ctx=ctx,
    )


async def write_app_bundle_storage(
    *,
    plan: _UploadPlan,
    source_archive_bytes: bytes | Path | None,
    dist_archive_bytes: bytes | Path | None,
) -> _WrittenBundle:
    """Write the uploaded bytes to app storage. Takes no unit of work: this is
    the phase that must not hold a pooled connection, and an operation that
    cannot be given one cannot hold one."""
    return await AppStoragePhase(_get_app_storage_factory()).write_bundle(
        plan, source_archive_bytes, dist_archive_bytes
    )


async def finalize_app_bundle_upload(
    uow, *, plan: _UploadPlan, written: _WrittenBundle, user_id: UUID
) -> AppEntity:
    """Persist the release and point the app at it, after the storage writes."""
    return await build_app_service(uow).finalize_upload_bundle(plan, written, user_id)


__all__ = [
    "create_app",
    "finalize_app_bundle_upload",
    "find_app_by_name",
    "list_app_names",
    "read_app_archive",
    "require_app",
    "resolve_app_bundle_upload",
    "resolve_app_dist_archive",
    "resolve_app_source_archive",
    "write_app_bundle_storage",
]
