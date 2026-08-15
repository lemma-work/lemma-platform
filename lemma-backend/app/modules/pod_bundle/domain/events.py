"""Pod-bundle domain events published to the ``pod_bundle_events`` stream.

The module's own progress state lives in Redis rather than Postgres, so these
two are staged through a short transaction at the terminal point rather than
riding one that already exists. That is a weaker version of the outbox guarantee
than the rest of the catalog enjoys, and it is worth it: the share-import-remix
loop is the product's growth loop, and it is unmeasurable server-side without
these. One tiny transaction per completed export or import is nothing beside a
BULK job that just zipped or rebuilt a whole pod.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.events import DomainEvent

POD_BUNDLE_EVENTS_STREAM = "pod_bundle_events"


class PodBundleDomainEvent(DomainEvent):
    @classmethod
    def stream_name(cls) -> str:
        return POD_BUNDLE_EVENTS_STREAM


class BundleExportedEvent(PodBundleDomainEvent):
    event_type: str = "pod_bundle.exported"
    bundle_id: UUID
    pod_id: UUID
    user_id: UUID | None = None
    resource_count: int = 0


class BundleImportCompletedEvent(PodBundleDomainEvent):
    event_type: str = "pod_bundle.import_completed"
    bundle_id: UUID
    pod_id: UUID
    user_id: UUID | None = None
    resource_count: int = 0
    is_remix: bool = False
