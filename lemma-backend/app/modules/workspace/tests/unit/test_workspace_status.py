"""What a person's computer is doing, for the page that waits on it."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fakeredis import aioredis as fake_aioredis

from app.modules.workspace.services import sandbox_progress

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _redis():
    fake = fake_aioredis.FakeRedis(decode_responses=True)
    sandbox_progress._use_store_for_tests(fake)
    yield fake
    sandbox_progress._use_store_for_tests(None)


async def test_a_fabric_downloading_its_image_reads_as_downloading():
    sandbox_id = uuid4()
    await sandbox_progress.record_phase(
        sandbox_id,
        "still downloading ghcr.io/lemma-work/lemma-workspace:test@sha256:d6dc",
    )
    phase = await sandbox_progress.current_phase(sandbox_id)
    assert phase is not None
    assert phase["phase"] == "downloading"
    assert "update" in phase["detail"], "says why it takes a while"


async def test_anything_else_not_ready_reads_as_starting():
    sandbox_id = uuid4()
    await sandbox_progress.record_phase(sandbox_id, "provider unavailable")
    phase = await sandbox_progress.current_phase(sandbox_id)
    assert phase is not None and phase["phase"] == "starting"


async def test_a_sandbox_that_came_up_has_no_phase():
    sandbox_id = uuid4()
    await sandbox_progress.record_phase(sandbox_id, "still downloading x")
    await sandbox_progress.clear_phase(sandbox_id)
    assert await sandbox_progress.current_phase(sandbox_id) is None


async def test_a_phase_expires_if_nobody_clears_it(_redis):
    """A worker that died mid-ensure must not leave "downloading" for good."""
    sandbox_id = uuid4()
    await sandbox_progress.record_phase(sandbox_id, "still downloading x")
    ttl = await _redis.ttl(f"workspace:sandbox-phase:{sandbox_id}")
    assert 0 < ttl <= sandbox_progress._PHASE_TTL_SECONDS


async def test_an_unreachable_store_is_unknown_not_an_error():
    class _Down:
        async def get(self, *_args, **_kwargs):
            raise ConnectionError("redis is down")

        async def set(self, *_args, **_kwargs):
            raise ConnectionError("redis is down")

    sandbox_progress._use_store_for_tests(_Down())
    sandbox_id = uuid4()
    await sandbox_progress.record_phase(sandbox_id, "still downloading x")
    assert await sandbox_progress.current_phase(sandbox_id) is None


async def test_a_download_the_guest_can_measure_carries_its_megabytes():
    sandbox_id = uuid4()
    await sandbox_progress.record_phase(
        sandbox_id,
        "still downloading ghcr.io/lemma-work/lemma-workspace@sha256:d6dc (412 MB of 980 MB)",
    )
    phase = await sandbox_progress.current_phase(sandbox_id)
    assert phase is not None
    assert phase["phase"] == "downloading"
    assert (phase["done_mb"], phase["total_mb"]) == (412, 980)


def test_a_download_the_guest_cannot_measure_yet_has_no_figures():
    phase = sandbox_progress.phase_for("still downloading ghcr.io/x@sha256:1")
    assert "done_mb" not in phase
