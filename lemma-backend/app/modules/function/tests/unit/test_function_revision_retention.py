"""Pruning function revisions: what gets deleted, and what must never be."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, uuid7

import pytest

from app.core.retention import RetentionPolicy
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRevisionEntity,
    FunctionStatus,
)
from app.modules.function.services.function_revision_retention import (
    FunctionRevisionRetention,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
_TIGHT = RetentionPolicy(keep_last=1, keep_days=0, max_keep=1)


def _hash(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def _revision(function_id, number, *, seed=None, age_days=100):
    revision_hash = _hash(seed or str(number + 1))
    return FunctionRevisionEntity(
        id=uuid7(),
        function_id=function_id,
        revision_number=number,
        revision_hash=revision_hash,
        code_path=f"revisions/{revision_hash.removeprefix('sha256:')}/function.py",
        created_at=NOW - timedelta(days=age_days, seconds=-number),
    )


def _function(**overrides):
    return FunctionEntity(
        id=overrides.pop("id", uuid4()),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="score_lead",
        status=FunctionStatus.READY,
        **overrides,
    )


def _retention(function, revisions, *, in_flight=frozenset()):
    repo = AsyncMock()
    repo.get_for_update.return_value = function
    repo.list_revisions.return_value = revisions
    repo.revision_hashes_with_runs_in_flight.return_value = set(in_flight)
    storage = AsyncMock()
    return FunctionRevisionRetention(repo, Mock(return_value=storage)), repo, storage


@pytest.mark.asyncio
async def test_pruning_deletes_the_artifact_and_the_source_directory():
    function = _function()
    old, live = _revision(function.id, 1, seed="a"), _revision(function.id, 2, seed="b")
    function.revision_hash = live.revision_hash
    retention, repo, storage = _retention(function, [old, live])

    plan = await retention.plan(function, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert repo.mark_revisions_pruned.await_args.args[0] == [old.id]
    storage.delete_file.assert_awaited_once_with(old.artifact_path)
    storage.delete_prefix.assert_awaited_once_with(old.code_path.rsplit("/", 1)[0])


@pytest.mark.asyncio
async def test_the_live_revision_survives_a_tight_policy():
    function = _function()
    live = _revision(function.id, 1)
    function.revision_hash = live.revision_hash
    retention, _repo, storage = _retention(function, [live])

    plan = await retention.plan(function, policy=_TIGHT, now=NOW)

    assert plan.is_empty
    storage.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_revision_with_a_run_in_flight_is_kept():
    """A run resolves its artifact from its own hash at execution time, so
    deleting it under a dispatched run makes that run fail instead of execute."""
    function = _function()
    old, live = _revision(function.id, 1, seed="a"), _revision(function.id, 2, seed="b")
    function.revision_hash = live.revision_hash
    retention, _repo, storage = _retention(
        function, [old, live], in_flight={old.revision_hash}
    )

    plan = await retention.plan(function, policy=_TIGHT, now=NOW)

    assert plan.is_empty
    storage.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_young_revision_without_runs_is_subject_to_the_limit():
    """A run is created and dispatched in separate steps, so a just-recorded
    revision can be pinned by a run that does not exist in the table yet."""
    function = _function()
    old = _revision(function.id, 1, seed="a", age_days=0)
    live = _revision(function.id, 2, seed="b", age_days=0)
    function.revision_hash = live.revision_hash
    retention, _repo, _storage = _retention(function, [old, live])

    plan = await retention.plan(function, policy=_TIGHT, now=NOW)

    assert plan.revision_numbers == (old.revision_number,)


@pytest.mark.asyncio
async def test_a_sweep_never_issues_a_bare_prefix_delete():
    """delete_prefix("") is the entire function's storage, including the live
    revision's artifact."""
    function = _function()
    old, live = _revision(function.id, 1, seed="a"), _revision(function.id, 2, seed="b")
    old.code_path = "function.py"  # no directory component
    function.revision_hash = live.revision_hash
    retention, _repo, storage = _retention(function, [old, live])

    plan = await retention.plan(function, policy=_TIGHT, now=NOW)
    await retention.execute(plan)

    assert all(call.args[0] for call in storage.delete_prefix.await_args_list)
