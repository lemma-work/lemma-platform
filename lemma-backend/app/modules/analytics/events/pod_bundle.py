"""Bundles exported out of a pod, and imported into one."""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import emit
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.analytics.events.wiring import actor_or_system, origin_of, router
from app.modules.analytics.services.buckets import COUNT_EDGES, bucket
from app.modules.pod_bundle.domain.events import (
    POD_BUNDLE_EVENTS_STREAM,
    BundleExportedEvent,
    BundleImportCompletedEvent,
)

WIRED = frozenset({"bundle.exported", "import.completed"})


@reliable_redis_stream_subscriber(
    router,
    POD_BUNDLE_EVENTS_STREAM,
    group="analytics-pod-bundle",
    consumer="analytics-pod-bundle-consumer",
)
async def on_pod_bundle_event(
    event: dict[str, object],
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
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
            pod_id=imported.pod_id,
            properties={
                "pod_id": imported.pod_id,
                "bundle_id": imported.bundle_id,
                "resource_count_bucket": bucket(imported.resource_count, COUNT_EDGES),
                "is_remix": imported.is_remix,
            },
        )

    await inbox.process("analytics.pod_bundle", event, record)
