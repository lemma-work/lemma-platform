"""Drop the pod schemas of pods that are gone.

Deleting a pod is a soft delete in the platform database; its schema, in the
datastore database, was never dropped, so schemas outnumbered live pods many
times over and anything that walked them grew with every pod ever created.

The two databases are separate, so this cannot be a join. It pages through the
datastore catalog, asks the pod module which of those pods are gone, and drops
only those -- a live pod, or one deleted inside the retention window, is never
in the answer. Bounded per run: a backlog drains over successive runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from app.core.log.log import get_logger
from app.modules.datastore.infrastructure.query_role import POD_SCHEMA_RE

logger = get_logger(__name__)

#: Schemas read from the catalog, and pod ids sent to the platform database, at
#: a time.
PAGE_SIZE = 500
#: Most schemas one run drops. Each drop takes an exclusive lock on everything
#: in the schema; spreading a large backlog over several nights keeps any one
#: run short.
MAX_DROPS_PER_RUN = 1000


class PodSchemaCatalogPort(Protocol):
    async def list_pod_schemas(self, *, after: str, limit: int) -> list[str]:
        """Up to ``limit`` pod schema names after ``after``, in name order."""

    async def drop_schema(self, schema_name: str) -> None:
        """Drop one pod schema and everything in it."""


type GonePods = Callable[[Collection[UUID], datetime], Awaitable[set[UUID]]]
"""Which of these pod ids are gone -- no row, or deleted before the cutoff."""


@dataclass(frozen=True, slots=True)
class ReapSummary:
    scanned: int = 0
    dropped: int = 0
    failed: int = 0


def pod_id_of(schema_name: str) -> UUID | None:
    """The pod a schema belongs to, or None for a name this module never made."""
    if not POD_SCHEMA_RE.fullmatch(schema_name):
        return None
    return UUID(schema_name.removeprefix("pod_").replace("_", "-"))


class OrphanSchemaReaper:
    def __init__(
        self,
        catalog: PodSchemaCatalogPort,
        gone_pods: GonePods,
        *,
        retention: timedelta,
        page_size: int = PAGE_SIZE,
        max_drops: int = MAX_DROPS_PER_RUN,
    ) -> None:
        self._catalog = catalog
        self._gone_pods = gone_pods
        self._retention = retention
        self._page_size = page_size
        self._max_drops = max_drops

    async def run(self, now: datetime) -> ReapSummary:
        cutoff = now - self._retention
        scanned = dropped = failed = 0
        after = ""
        while dropped + failed < self._max_drops:
            names = await self._catalog.list_pod_schemas(
                after=after, limit=self._page_size
            )
            if not names:
                break
            after = names[-1]
            scanned += len(names)
            by_pod = {
                pod_id: name
                for name in names
                if (pod_id := pod_id_of(name)) is not None
            }
            gone = await self._gone_pods(by_pod.keys(), cutoff)
            for pod_id in sorted(gone)[: self._max_drops - dropped - failed]:
                if await self._drop(by_pod[pod_id]):
                    dropped += 1
                else:
                    failed += 1
        return ReapSummary(scanned=scanned, dropped=dropped, failed=failed)

    async def _drop(self, schema_name: str) -> bool:
        """One schema; a failure is logged and the run moves on to the next."""
        try:
            await self._catalog.drop_schema(schema_name)
        except DBAPIError:
            logger.warning(
                "datastore.orphan_schemas.drop.degraded",
                schema_name=schema_name,
                exc_info=True,
            )
            return False
        logger.info("datastore.orphan_schemas.dropped", schema_name=schema_name)
        return True
