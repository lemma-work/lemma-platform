"""The reaper drops exactly what the pod module says is gone, and stops in time.

Against the real catalog and the real pods table, ``test_orphan_schemas_e2e.py``
pins which pods count as gone. These pin the paging, the per-run bound and the
refusal to touch any schema this module did not name.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy.exc import DBAPIError

from app.modules.datastore.services.orphan_schema_reaper import (
    OrphanSchemaReaper,
    pod_id_of,
)

NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)


def _schema(pod_id: UUID) -> str:
    return f"pod_{str(pod_id).replace('-', '_')}"


class _Catalog:
    def __init__(self, names: list[str], *, failing: set[str] = frozenset()):
        self.names = sorted(names)
        self.failing = failing
        self.dropped: list[str] = []
        self.pages = 0

    async def list_pod_schemas(self, *, after: str, limit: int) -> list[str]:
        self.pages += 1
        return [name for name in self.names if name > after][:limit]

    async def drop_schema(self, schema_name: str) -> None:
        if schema_name in self.failing:
            raise DBAPIError("DROP SCHEMA", {}, Exception("lock timeout"))
        self.dropped.append(schema_name)


class _Pods:
    """Answers like the pod module: these ids are gone, the rest are kept."""

    def __init__(self, gone: set[UUID]):
        self.gone = gone
        self.cutoffs: list[datetime] = []

    async def __call__(
        self, pod_ids: Collection[UUID], deleted_before: datetime
    ) -> set[UUID]:
        self.cutoffs.append(deleted_before)
        return self.gone & set(pod_ids)


def _reaper(catalog, pods, **kwargs) -> OrphanSchemaReaper:
    return OrphanSchemaReaper(catalog, pods, retention=timedelta(days=30), **kwargs)


async def test_only_the_gone_pods_schemas_are_dropped() -> None:
    live, gone = uuid4(), uuid4()
    catalog = _Catalog([_schema(live), _schema(gone)])
    pods = _Pods({gone})

    summary = await _reaper(catalog, pods).run(NOW)

    assert catalog.dropped == [_schema(gone)]
    assert (summary.scanned, summary.dropped, summary.failed) == (2, 1, 0)
    assert pods.cutoffs == [NOW - timedelta(days=30)]


async def test_a_schema_this_module_did_not_name_is_never_asked_about() -> None:
    gone = uuid4()
    catalog = _Catalog(["pod_not_a_uuid", "pod_" + "0" * 32, _schema(gone)])

    await _reaper(catalog, _Pods({gone})).run(NOW)

    assert catalog.dropped == [_schema(gone)]
    assert pod_id_of("pod_not_a_uuid") is None


async def test_every_page_is_read_until_the_catalog_runs_out() -> None:
    pod_ids = [uuid4() for _ in range(7)]
    catalog = _Catalog([_schema(pod_id) for pod_id in pod_ids])

    summary = await _reaper(catalog, _Pods(set(pod_ids)), page_size=3).run(NOW)

    assert summary.dropped == 7
    assert catalog.pages == 4  # three full or partial pages, then an empty one


async def test_a_run_stops_at_its_drop_bound() -> None:
    pod_ids = [uuid4() for _ in range(10)]
    catalog = _Catalog([_schema(pod_id) for pod_id in pod_ids])

    summary = await _reaper(catalog, _Pods(set(pod_ids)), max_drops=4).run(NOW)

    assert summary.dropped == 4
    assert len(catalog.dropped) == 4


async def test_one_failed_drop_does_not_stop_the_rest() -> None:
    first, second = sorted([uuid4(), uuid4()])
    catalog = _Catalog([_schema(first), _schema(second)], failing={_schema(first)})

    summary = await _reaper(catalog, _Pods({first, second})).run(NOW)

    assert catalog.dropped == [_schema(second)]
    assert (summary.dropped, summary.failed) == (1, 1)


def test_a_schema_name_maps_back_to_its_pod() -> None:
    pod_id = uuid4()
    assert pod_id_of(_schema(pod_id)) == pod_id
