"""A new pod's datastore schema, created by a work item written with the pod.

The pod row and its ``pod.provision`` item commit together, so a pod cannot
exist without the work that gives it a schema -- the stream consumer this
replaces could lose that wake, and paths that ran before it caught up made the
schema lazily, which did not cover everything. A JOB in the ``events`` class
because it writes the datastore database; idempotent, so a retry, a duplicate
or the lazy path having got there first all cost a catalog read.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.infrastructure.work import (
    JobContext,
    Unavailable,
    UnavailableEvidence,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.pod.contracts.lifecycle import PodCreated

POD_PROVISION = "pod.provision"


class PodProvisionPayload(BaseModel):
    pod_id: UUID


async def run_pod_provision(ctx: JobContext, payload: PodProvisionPayload) -> None:
    from app.modules.datastore.services.wiring import get_schema_manager

    # The process singleton: it remembers the query role is already ensured,
    # so each pod costs no extra catalog probe.
    manager = get_schema_manager()
    try:
        await manager.create_datastore_schema(payload.pod_id)
        # Born at the latest search version: the tables are empty, so this is a
        # few catalog writes, and the backfill never has to visit the pod.
        await manager.migrate_search_tables(payload.pod_id)
    except (OSError, OperationalError, InterfaceError) as exc:
        raise Unavailable(
            f"datastore database unreachable: {type(exc).__name__}",
            evidence=UnavailableEvidence.CONNECTION_REFUSED,
        ) from exc
    del ctx


def register_pod_provision_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=POD_PROVISION,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=PodProvisionPayload,
    )(run_pod_provision)


register_pod_provision_work(default_registry)
facts.subscribe(
    PodCreated,
    kind=POD_PROVISION,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"pod-provision:{fact.pod_id}",
)
