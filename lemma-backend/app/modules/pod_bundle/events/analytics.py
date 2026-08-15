"""Stage the two bundle events that make the share -> import -> remix loop
measurable server-side.

The module's progress state lives in Redis, so unlike everywhere else in the
catalog there is no transaction already open at the terminal point to ride. A
short one is opened here instead. That is a weaker guarantee than the outbox
gives the rest of the catalog, and it is worth it: this loop is the product's
growth loop and is otherwise only half-measurable from the browser. One tiny
transaction is nothing beside a BULK job that just zipped or rebuilt a pod.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.scope import uow_scope
from app.modules.pod_bundle.domain.events import (
    BundleExportedEvent,
    BundleImportCompletedEvent,
)


async def record_bundle_exported(
    worker_ctx, *, export_id: UUID, pod_id: UUID, user_id: UUID, resource_count: int
) -> None:
    async with uow_scope(worker_ctx.uow_factory) as uow:
        uow.collect_events(
            [
                BundleExportedEvent(
                    bundle_id=export_id,
                    pod_id=pod_id,
                    user_id=user_id,
                    resource_count=resource_count,
                )
            ]
        )


async def record_import_completed(
    worker_ctx,
    *,
    import_id: UUID,
    pod_id: UUID,
    user_id: UUID,
    resource_count: int,
    is_remix: bool,
) -> None:
    async with uow_scope(worker_ctx.uow_factory) as uow:
        uow.collect_events(
            [
                BundleImportCompletedEvent(
                    bundle_id=import_id,
                    pod_id=pod_id,
                    user_id=user_id,
                    resource_count=resource_count,
                    is_remix=is_remix,
                )
            ]
        )
