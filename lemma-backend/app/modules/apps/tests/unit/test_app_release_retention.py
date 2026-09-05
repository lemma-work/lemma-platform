"""Pruning app releases: what gets deleted, and what must never be."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, uuid7

import pytest

from app.core.retention import RetentionPolicy
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity
from app.modules.apps.services.app_release_retention import AppReleaseRetention

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
_TIGHT = RetentionPolicy(keep_last=1, keep_days=0, max_keep=1)


def _release(app_id, number, *, source="source/aa/archive.zip", age_days=100):
    root = f"releases/{number:064x}/dist/"
    return AppReleaseEntity(
        id=uuid7(),
        app_id=app_id,
        version=f"{number:064x}",
        release_number=number,
        dist_root_path=root,
        dist_archive_path=f"{root}archive.zip",
        source_archive_path=source,
        created_at=NOW - timedelta(days=age_days, seconds=-number),
    )


def _retention(app, releases):
    repo = AsyncMock()
    repo.get_for_update.return_value = app
    repo.list_releases.return_value = releases
    storage = AsyncMock()
    return AppReleaseRetention(repo, Mock(return_value=storage)), repo, storage


def _app(**overrides):
    return AppEntity(
        id=overrides.pop("id", uuid4()),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="orders",
        public_slug="orders",
        **overrides,
    )


@pytest.mark.asyncio
async def test_pruning_deletes_the_release_prefix_and_marks_the_row():
    app = _app()
    old, live = _release(app.id, 1), _release(app.id, 2)
    app.current_release_id = live.id
    app.source_archive_path = live.source_archive_path
    retention, repo, storage = _retention(app, [old, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    # Marked before the bytes go: a sweep that dies midway must not leave a
    # release the UI still offers to promote.
    assert repo.mark_releases_pruned.await_args.args[0] == [old.id]
    storage.delete_prefix.assert_awaited_once_with(old.dist_root_path)


@pytest.mark.asyncio
async def test_the_live_release_survives_a_tight_policy():
    app = _app()
    live = _release(app.id, 1)
    app.current_release_id = live.id
    retention, repo, storage = _retention(app, [live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)

    assert plan.is_empty
    repo.mark_releases_pruned.assert_not_awaited()
    storage.delete_prefix.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_shared_with_a_retained_release_is_not_deleted():
    """Source is content-addressed, so a dist-only change produces a new release
    pointing at the SAME source blob. Deleting it with the older release would
    strip the source from a release that is still listed."""
    app = _app()
    shared = "source/same/archive.zip"
    old = _release(app.id, 1, source=shared)
    live = _release(app.id, 2, source=shared)
    app.current_release_id = live.id
    app.source_archive_path = shared
    retention, _repo, storage = _retention(app, [old, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert plan.source_archives == ()
    deleted = [call.args[0] for call in storage.delete_file.await_args_list]
    assert shared not in deleted


@pytest.mark.asyncio
async def test_source_no_retained_release_references_is_deleted():
    app = _app()
    orphaned = "source/old/archive.zip"
    old = _release(app.id, 1, source=orphaned)
    live = _release(app.id, 2, source="source/new/archive.zip")
    app.current_release_id = live.id
    app.source_archive_path = live.source_archive_path
    retention, _repo, storage = _retention(app, [old, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert plan.source_archives == (orphaned,)
    assert orphaned in [call.args[0] for call in storage.delete_file.await_args_list]


@pytest.mark.asyncio
async def test_a_sweep_never_issues_a_bare_prefix_delete():
    """delete_prefix("") is the whole app's storage -- on a bucket-root store it
    would take the bucket with it."""
    app = _app()
    old = _release(app.id, 1)
    old.dist_root_path = ""
    live = _release(app.id, 2)
    app.current_release_id = live.id
    retention, _repo, storage = _retention(app, [old, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert all(call.args[0] for call in storage.delete_prefix.await_args_list)


@pytest.mark.asyncio
async def test_a_release_whose_archive_sits_outside_its_root_is_not_treated_as_empty():
    """The only thing standing between that archive and the delete was is_empty.

    An empty `dist_root_path` keeps the release out of `dist_roots`, and a source
    blob the live release still shares keeps it out of `source_archives` -- so the
    plan's only content is `dist_archives`, which `is_empty` did not look at. It
    returned early and the archive was never deleted.
    """
    app = _app()
    old = _release(app.id, 1)
    old.dist_root_path = ""
    live = _release(app.id, 2)
    app.current_release_id = live.id
    app.source_archive_path = live.source_archive_path
    retention, _repo, storage = _retention(app, [old, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    assert plan.dist_roots == ()
    assert plan.source_archives == ()
    assert plan.dist_archives == (old.dist_archive_path,)
    assert plan.is_empty is False

    await retention.execute(plan)
    deleted = [call.args[0] for call in storage.delete_file.await_args_list]
    assert old.dist_archive_path in deleted


@pytest.mark.asyncio
async def test_an_archive_inside_its_release_prefix_is_not_deleted_twice():
    app = _app()
    old, live = _release(app.id, 1), _release(app.id, 2)
    app.current_release_id = live.id
    retention, _repo, _storage = _retention(app, [old, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)

    # It sits under dist_root_path, so the prefix delete already covers it.
    assert plan.dist_archives == ()


# -- unfinished prunes -------------------------------------------------------
#
# `plan` commits the tombstone and deletes the bytes afterwards, outside the
# unit of work. A process that dies in between leaves a row saying "removed"
# over bytes that are not -- and `select_prunable` skips already-pruned rows, so
# nothing ever came back for them.


@pytest.mark.asyncio
async def test_a_prune_that_died_before_deleting_is_retried():
    app = _app()
    stale, live = _release(app.id, 1), _release(app.id, 2)
    stale.pruned_at = NOW - timedelta(hours=1)
    app.current_release_id = live.id
    app.source_archive_path = live.source_archive_path
    retention, repo, storage = _retention(app, [stale, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert stale.dist_root_path in plan.dist_roots
    assert stale.dist_root_path in [
        call.args[0] for call in storage.delete_prefix.await_args_list
    ]
    # Nothing new to stamp, and re-running a delete is not a new prune.
    repo.mark_releases_pruned.assert_not_awaited()
    assert plan.release_numbers == ()


@pytest.mark.asyncio
async def test_completed_deletion_is_not_retried():

    app = _app()
    ancient, live = _release(app.id, 1), _release(app.id, 2)
    ancient.pruned_at = NOW - timedelta(days=30)
    ancient.purged_at = NOW - timedelta(days=29)
    app.current_release_id = live.id
    app.source_archive_path = live.source_archive_path
    retention, _repo, storage = _retention(app, [ancient, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert plan.is_empty
    storage.delete_prefix.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_never_deletes_the_live_release():
    """An install damaged by the redeploy-onto-a-pruned-digest bug has exactly
    this shape: a live release still carrying pruned_at. Re-deleting its bytes
    would turn a stale row into a real outage."""
    app = _app()
    live = _release(app.id, 1)
    live.pruned_at = NOW - timedelta(hours=1)
    app.current_release_id = live.id
    retention, _repo, storage = _retention(app, [live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert live.dist_root_path not in plan.dist_roots
    storage.delete_prefix.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_keeps_source_shared_with_a_retained_release():
    """The stale row goes through the same shared-blob filter as a fresh one;
    source is content-addressed, so its blob may belong to a release that stays."""
    app = _app()
    stale, live = _release(app.id, 1), _release(app.id, 2)
    stale.pruned_at = NOW - timedelta(hours=1)
    app.current_release_id = live.id
    app.source_archive_path = live.source_archive_path
    retention, _repo, _storage = _retention(app, [stale, live])

    plan = await retention.plan(app, policy=_TIGHT, now=NOW)

    assert stale.source_archive_path == live.source_archive_path
    assert plan.source_archives == ()
