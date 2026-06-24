from __future__ import annotations

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.modules.identity.domain.errors import UserConflictError, UserNotFoundError
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.services.user_service import UserService


@pytest.mark.asyncio
async def test_create_user_emits_signup_event_without_personal_org(
    user_service: UserService,
    user_repository_mock: AsyncMock,
    organization_repository_mock: AsyncMock,
):
    user = UserEntity(email="test+user@example.com")

    user_repository_mock.get_by_email.return_value = None
    user_repository_mock.create.return_value = user

    created = await user_service.create_user(user)

    assert created == user

    create_user_arg = user_repository_mock.create.await_args.args[0]
    events = create_user_arg.collect_events()
    assert len(events) == 1
    assert events[0].event_type == "identity.user.signed_up"
    organization_repository_mock.create.assert_not_awaited()
    organization_repository_mock.add_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_raises_conflict_when_email_exists(
    user_service: UserService,
    user_repository_mock: AsyncMock,
):
    user_repository_mock.get_by_email.return_value = UserEntity(email="test+user@example.com")

    with pytest.raises(UserConflictError):
        await user_service.create_user(UserEntity(email="test+user@example.com"))


@pytest.mark.asyncio
async def test_get_user_raises_not_found(
    user_service: UserService,
    user_repository_mock: AsyncMock,
):
    user_repository_mock.get.return_value = None

    with pytest.raises(UserNotFoundError):
        await user_service.get_user(uuid4())


@pytest.mark.asyncio
async def test_get_user_returns_entity(
    user_service: UserService,
    user_repository_mock: AsyncMock,
    user_cache_mock: AsyncMock,
):
    user = UserEntity(email="test+user@example.com")
    user_cache_mock.get.return_value = None
    user_repository_mock.get.return_value = user

    result = await user_service.get_user(user.id)

    assert result == user
    user_cache_mock.set.assert_awaited_once_with(user)


@pytest.mark.asyncio
async def test_get_user_returns_cached_entity_without_db_hit(
    user_service: UserService,
    user_repository_mock: AsyncMock,
    user_cache_mock: AsyncMock,
):
    user = UserEntity(email="test+cached@example.com")
    user_cache_mock.get.return_value = user

    result = await user_service.get_user(user.id)

    assert result == user
    user_repository_mock.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_user_by_email_returns_optional(
    user_service: UserService,
    user_repository_mock: AsyncMock,
):
    user = UserEntity(email="test+user@example.com")
    user_repository_mock.get_by_email.return_value = user

    assert await user_service.get_user_by_email("test+user@example.com") == user


@pytest.mark.asyncio
async def test_update_user_delegates_to_repository(
    user_service: UserService,
    user_repository_mock: AsyncMock,
    user_cache_mock: AsyncMock,
):
    user = UserEntity(email="test+user@example.com")
    user_repository_mock.update.return_value = user

    updated = await user_service.update_user(user)

    assert updated == user
    user_repository_mock.update.assert_awaited_once_with(user)
    user_cache_mock.set.assert_awaited_once_with(user)
