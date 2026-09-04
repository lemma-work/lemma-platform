"""When a function's grants change, and when its cached environment is dropped.

Two properties, neither of which had a test before, and one of which was wrong:

- The sandbox reads its grants from a cached environment, so a grant write that
  does not drop that cache leaves the function running with the access it had
  before. A dropped invalidation is an authorization staleness bug.
- The drop has to happen *after* the write commits. Inside the transaction it
  both pins a pooled Postgres connection across a Redis round trip and races: a
  concurrent reader can repopulate the cache from the pre-commit state between
  the invalidation and the commit.

No doubles here. `write_then_invalidate` takes both halves as parameters
precisely so the order can be asserted with plain fakes rather than by patching
names inside the module under test.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.function.api.controllers.function_grants import (
    apply_function_grants,
    write_then_invalidate,
)

pytestmark = pytest.mark.unit


class _Uow:
    """A unit of work that records the moment it commits."""

    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.session = object()

    async def __aenter__(self) -> "_Uow":
        return self

    async def __aexit__(self, *exc) -> bool:
        self._log.append("committed")
        return False


def _factory(log: list[str]):
    return lambda: _Uow(log)


def _writer(log: list[str], *, applied: bool):
    async def _write(session):
        del session
        log.append("granted")
        return applied

    return _write


def _invalidator(log: list[str]):
    async def _invalidate():
        log.append("invalidated")

    return _invalidate


@pytest.mark.asyncio
async def test_the_cache_is_dropped_only_after_the_grant_write_commits():
    log: list[str] = []

    await write_then_invalidate(
        _factory(log),
        write=_writer(log, applied=True),
        invalidate=_invalidator(log),
    )

    assert log == ["granted", "committed", "invalidated"], (
        "the cache was dropped inside the transaction, where a concurrent "
        "reader can refill it from the pre-commit state"
    )


@pytest.mark.asyncio
async def test_a_write_that_changed_nothing_drops_no_cache():
    """An unchanged grantee has nothing stale, and the sandbox keeps its warm
    environment."""
    log: list[str] = []

    await write_then_invalidate(
        _factory(log),
        write=_writer(log, applied=False),
        invalidate=_invalidator(log),
    )

    assert log == ["granted", "committed"]


@pytest.mark.asyncio
async def test_a_payload_that_says_nothing_about_permissions_opens_no_connection():
    """An absent block leaves the grants alone, which is the common create.

    The factory raises rather than returning a fake: the assertion is that
    nothing asks it for a connection at all.
    """

    def _explodes():
        raise AssertionError("checked out a connection with nothing to write")

    await apply_function_grants(
        _explodes,
        pod_id=uuid4(),
        function=SimpleNamespace(id=uuid4()),
        data=SimpleNamespace(permissions=None),
        user=SimpleNamespace(id=uuid4()),
    )
