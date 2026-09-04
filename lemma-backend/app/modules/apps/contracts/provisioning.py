"""What another module does to a pod's apps when it builds one.

Five operations, not `AppService`. The archive pair keeps the discipline the
service documents and a handed-out service object cannot enforce: resolving an
archive's location is a DB read, reading its bytes is not, and they are separate
calls so the transfer does not hold a pooled connection for its whole length.

A submodule rather than `contracts/__init__`: this reaches the service layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.modules.apps.api.dependencies import build_app_service
from app.modules.apps.domain.entities import AppEntity


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


__all__ = [
    "list_app_names",
    "read_app_archive",
    "require_app",
    "resolve_app_dist_archive",
    "resolve_app_source_archive",
]
