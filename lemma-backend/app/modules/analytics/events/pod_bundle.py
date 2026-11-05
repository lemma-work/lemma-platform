"""Bundles exported out of a pod, and imported into one."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import emit
from app.modules.analytics.events.wiring import (
    ProjectionReads,
    actor_or_system,
    origin_of,
)
from app.modules.analytics.services.buckets import COUNT_EDGES, bucket
from app.modules.pod_bundle.domain.events import (
    BundleExportedEvent,
    BundleImportCompletedEvent,
)

WIRED = frozenset({"bundle.exported", "import.completed"})


@projection(
    BundleExportedEvent.get_event_type(),
    BundleImportCompletedEvent.get_event_type(),
)
async def on_pod_bundle_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
    event_type = event.get("event_type")
    if event_type not in {
        BundleExportedEvent.get_event_type(),
        BundleImportCompletedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == BundleExportedEvent.get_event_type():
            exported = BundleExportedEvent.model_validate(event)
            emit(
                "bundle.exported",
                actor=actor_or_system(exported.user_id),
                origin=origin,
                organization_id=await reads.organization_of_pod(exported.pod_id),
                pod_id=exported.pod_id,
                properties={
                    "pod_id": exported.pod_id,
                    "bundle_id": exported.bundle_id,
                    "resource_count_bucket": bucket(
                        exported.resource_count, COUNT_EDGES
                    ),
                },
            )
            return
        imported = BundleImportCompletedEvent.model_validate(event)
        emit(
            "import.completed",
            actor=actor_or_system(imported.user_id),
            origin=origin,
            organization_id=await reads.organization_of_pod(imported.pod_id),
            pod_id=imported.pod_id,
            properties={
                "pod_id": imported.pod_id,
                # The export's id, read back out of the archive: the join to
                # `bundle.exported`. Absent for a bundle no export produced,
                # never the import's own job id.
                "bundle_id": imported.bundle_id,
                "resource_count_bucket": bucket(imported.resource_count, COUNT_EDGES),
            },
        )

    await record()
