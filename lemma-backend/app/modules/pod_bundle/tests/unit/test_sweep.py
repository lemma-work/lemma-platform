"""Sweep helper: reclaim orphaned staging, recover stuck jobs, keep active ones."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.events import sweep as sweep_module
from app.modules.pod_bundle.events.sweep import _STUCK_AFTER_SECONDS, _sweep


class FakeStore:
    def __init__(self, imports):
        self._imports = {s.import_id: s for s in imports}
        self.saved = []

    async def get_import(self, job_id):
        return self._imports.get(job_id)

    async def save_import(self, state):
        state.touch()
        self.saved.append(state)

    async def get_export(self, job_id):
        return None

    async def save_export(self, state):
        self.saved.append(state)

    async def get_publish(self, job_id):
        return None

    async def save_publish(self, state):
        self.saved.append(state)

    async def recover_stale_jobs(self, *, cutoff):
        del cutoff
        return []


class FakeStaging:
    def __init__(self, import_ids):
        self._by_kind = {
            "pod-imports": list(import_ids),
            "pod-exports": [],
            "pod-publishes": [],
        }
        self.deleted = []

    async def list_archives(self, kind):
        return [(jid, None) for jid in self._by_kind.get(kind, [])]

    async def delete_archive(self, kind, job_id):
        self.deleted.append((kind, job_id))


@pytest.fixture(autouse=True)
def no_row_purge(monkeypatch):
    """Row retention talks to PostgreSQL; these cases are about the archive and
    stuck-job halves of the sweep. The retention case patches its own."""

    async def _none(*, cutoff, limit):
        return 0

    monkeypatch.setattr(sweep_module, "purge_completed_jobs", _none)


def _state(status, *, age_seconds=0) -> ImportState:
    s = ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind="URL"),
        status=status,
    )
    s.updated_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return s


async def test_orphaned_archive_is_reclaimed():
    # Archive present but its state is gone (TTL expired) → delete.
    orphan_id = uuid4()
    store = FakeStore([])
    staging = FakeStaging([orphan_id])
    reclaimed, recovered, _purged = await _sweep(store, staging)
    assert reclaimed == 1 and recovered == 0
    assert staging.deleted == [("pod-imports", orphan_id)]


async def test_stuck_job_is_marked_failed_and_kept():
    stuck = _state(ImportStatus.APPLYING, age_seconds=_STUCK_AFTER_SECONDS + 60)
    store = FakeStore([stuck])
    staging = FakeStaging([stuck.import_id])
    reclaimed, recovered, _purged = await _sweep(store, staging)
    assert reclaimed == 0 and recovered == 1
    assert stuck.status == ImportStatus.FAILED
    # Staging kept so the import can be retried; only recovered, not deleted.
    assert staging.deleted == []


async def test_active_job_is_left_alone():
    active = _state(ImportStatus.APPLYING, age_seconds=10)  # recently updated
    store = FakeStore([active])
    staging = FakeStaging([active.import_id])
    reclaimed, recovered, _purged = await _sweep(store, staging)
    assert reclaimed == 0 and recovered == 0
    assert active.status == ImportStatus.APPLYING
    assert staging.deleted == []


async def test_terminal_job_with_state_is_left_until_ttl():
    done = _state(ImportStatus.COMPLETED, age_seconds=_STUCK_AFTER_SECONDS + 60)
    store = FakeStore([done])
    staging = FakeStaging([done.import_id])
    reclaimed, recovered, _purged = await _sweep(store, staging)
    # Terminal + state still present → nothing to do (TTL will expire it).
    assert reclaimed == 0 and recovered == 0
    assert staging.deleted == []


async def test_finished_job_rows_are_purged_past_retention(monkeypatch):
    """`pod_bundle_jobs` grows a row per job plus one per plan step and nothing
    ever deleted them, though the table has carried a retention index on
    `completed_at` since it was created."""
    from app.modules.pod_bundle.infrastructure.job_retention import (
        JOB_ROW_PURGE_LIMIT,
        JOB_ROW_RETENTION_SECONDS,
    )

    calls = []

    async def _purge(*, cutoff, limit):
        calls.append((cutoff, limit))
        return 7

    monkeypatch.setattr(sweep_module, "purge_completed_jobs", _purge)
    _reclaimed, _recovered, purged = await _sweep(FakeStore([]), FakeStaging([]))

    assert purged == 7
    (cutoff, limit) = calls[0]
    assert limit == JOB_ROW_PURGE_LIMIT
    age = datetime.now(timezone.utc) - cutoff
    assert abs(age.total_seconds() - JOB_ROW_RETENTION_SECONDS) < 60
