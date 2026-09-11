"""A user-cache write must wait for the commit it describes.

`create_user`, `update_user` and `mark_email_verified` wrote the cache inline,
immediately after the repository write and with the transaction still open. That
is a pooled connection held across a Redis round trip on the signup, profile and
email-verification paths -- and it is the wrong order: a rollback leaves a cache
entry for a user that was never committed, and the next reader believes it.

The read path (`get_user`) uses `connection_released` instead, and correctly:
it has written nothing, so `safe_to_release` says yes. A write path cannot use
it -- `safe_to_release` refuses once the session is dirty, so the release would
be a silent no-op. These tests pin both halves of that distinction.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.services.user_service import UserService


class _Uow:
    """Records what was deferred instead of running it."""

    def __init__(self) -> None:
        self.deferred: list = []

    def after_commit(self, callback) -> None:
        self.deferred.append(callback)


def _service(uow: _Uow | None) -> tuple[UserService, AsyncMock]:
    cache = AsyncMock()
    repository = AsyncMock()
    repository.session = SimpleNamespace(
        info={"lemma_uow": uow} if uow is not None else {}
    )
    service = UserService(
        user_repository=repository,
        organization_repository=AsyncMock(),
        user_cache=cache,
    )
    return service, cache


def _user() -> UserEntity:
    return UserEntity(id=uuid7(), email="a@example.com", name="A")


@pytest.mark.asyncio
async def test_the_cache_write_is_deferred_until_after_the_commit() -> None:
    uow = _Uow()
    service, cache = _service(uow)

    await service._cache_after_commit(_user())

    cache.set.assert_not_awaited()
    assert len(uow.deferred) == 1, "the cache write ran inside the transaction"

    await uow.deferred[0]()
    cache.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_rolled_back_write_never_reaches_the_cache() -> None:
    """The correctness half, not the performance half.

    A deferred callback that is never invoked is exactly what a rollback looks
    like, and the cache must still be empty afterwards.
    """
    uow = _Uow()
    service, cache = _service(uow)

    await service._cache_after_commit(_user())

    cache.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_unit_of_work_it_caches_immediately() -> None:
    """No unit of work is also no pooled connection, so there is nothing to hold."""
    service, cache = _service(None)

    await service._cache_after_commit(_user())

    cache.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_cache_configured_is_a_no_op() -> None:
    repository = AsyncMock()
    repository.session = SimpleNamespace(info={})
    service = UserService(
        user_repository=repository,
        organization_repository=AsyncMock(),
        user_cache=None,
    )

    await service._cache_after_commit(_user())  # must not raise
