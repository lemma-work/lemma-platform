"""Pod-schema DDL as work: one ``pod_schema.migrate`` item per schema behind.

A boot hook that walked every pod schema would grow with every pod ever made
until it owned the boot. Instead a periodic ``pod_schema.backfill``, armed at
the first worker boot of a release that knows a new version, pages
through the schemas that are not at the latest version and enqueues one
``admin`` item per schema, in the schema's pod's namespace, so the admin class's
per-namespace cap and round robin pace it and no pod waits behind another's
migration. Each item takes only that schema's advisory lock and is idempotent,
so a duplicate, a retry or a second worker doing the same schema costs a
version read. Indexing still migrates its own schema inline on first use, and
a record write only makes sure the change log exists
(``ensure_change_log``): a cheap read, and the change log's own small DDL for
a schema the backfill has not reached -- never the chunk rewrite.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, field_validator

from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    pod_namespace,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    POD_SCHEMA_LATEST,
    list_schemas_below,
    migrate_pod_schema,
    pod_id_for_schema,
)
from app.modules.datastore.infrastructure.query_role import POD_SCHEMA_RE
from app.modules.datastore.infrastructure.session import get_datastore_engine

logger = get_logger(__name__)

POD_SCHEMA_MIGRATE = "pod_schema.migrate"
POD_SCHEMA_BACKFILL = "pod_schema.backfill"
#: Schemas enqueued per backfill page; the next page is its own item.
BACKFILL_PAGE = 500
BACKFILL_EVERY = timedelta(hours=6)
BACKFILL_PERIODIC = f"{POD_SCHEMA_BACKFILL}:v{POD_SCHEMA_LATEST}"


class PodSchemaMigratePayload(BaseModel):
    schema_name: str
    target: int = POD_SCHEMA_LATEST

    @field_validator("schema_name")
    @classmethod
    def _pod_schema(cls, value: str) -> str:
        if not POD_SCHEMA_RE.fullmatch(value):
            raise ValueError(f"not a pod schema: {value!r}")
        return value


class PodSchemaBackfillPayload(BaseModel):
    #: Keyset cursor: the last schema name a previous page covered.
    after: str = ""


async def run_migrate(ctx: JobContext, payload: PodSchemaMigratePayload) -> None:
    # create=False: a pod deleted after the backfill listed it keeps its
    # schema dropped.
    await migrate_pod_schema(
        get_datastore_engine(),
        payload.schema_name,
        target=payload.target,
        create=False,
    )
    del ctx


async def run_backfill(ctx: JobContext, payload: PodSchemaBackfillPayload) -> None:
    behind, cursor = await list_schemas_below(
        get_datastore_engine(),
        POD_SCHEMA_LATEST,
        after=payload.after,
        limit=BACKFILL_PAGE,
    )
    async with ctx.complete_work() as uow:
        work = ctx.writer(uow)
        for schema_name in behind:
            await work.add(
                POD_SCHEMA_MIGRATE,
                pod_namespace(pod_id_for_schema(schema_name)),
                PodSchemaMigratePayload(schema_name=schema_name),
                dedup_key=f"pod_schema:{schema_name}:v{POD_SCHEMA_LATEST}",
            )
        if cursor is not None:
            await work.add(
                POD_SCHEMA_BACKFILL,
                ctx.item.ns,
                PodSchemaBackfillPayload(after=cursor),
                dedup_key=f"pod_schema.backfill:{cursor}",
            )
    if behind:
        logger.info(
            "datastore.pod_schema.backfill_page.observed",
            enqueued=len(behind),
            more=cursor is not None,
        )


def register_pod_schema_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=POD_SCHEMA_MIGRATE,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=PodSchemaMigratePayload,
    )(run_migrate)
    registry.handler(
        kind=POD_SCHEMA_BACKFILL,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=PodSchemaBackfillPayload,
    )(run_backfill)
    # Named by the version it migrates to: a release that adds a version arms
    # a new chain at its first worker boot instead of waiting for the old
    # chain's next tick, and the old chain's queued item runs once and stops
    # (nothing re-arms a periodic no release registers).
    registry.periodic(BACKFILL_PERIODIC, BACKFILL_EVERY, POD_SCHEMA_BACKFILL)


register_pod_schema_work(default_registry)
