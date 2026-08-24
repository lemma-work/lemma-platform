"""Provisioning `/memory` must never cost the caller their request.

The service had a guard for exactly this and it had never fired: it caught
`DatastoreAccessDeniedError`, while the denial that actually arrives is a
`DomainError` raised by the authorization context. Nothing tested the service at
all, so the dead branch read as working code — until memory went on by default
and every creator without `folder.write` got a 403 instead of an agent.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.services import agent_memory_grant as grant_mod
from app.modules.agent.services.agent_memory_grant import sync_memory_folder_grant
from app.modules.datastore.contracts import DatastoreConflictError


def _uow():
    return SimpleNamespace(session=object())


def _file_service(monkeypatch, create_folder: AsyncMock):
    monkeypatch.setattr(
        grant_mod,
        "build_file_service",
        lambda uow: SimpleNamespace(create_folder=create_folder),
    )
    return create_folder


def _grant_spies(monkeypatch, *, folder_id=None):
    """Stand in for the three authorization helpers the service calls."""
    replace = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(grant_mod, "replace_resource_grantee_grant", replace)
    monkeypatch.setattr(grant_mod, "delete_resource_grantee_grant", delete)
    monkeypatch.setattr(
        grant_mod,
        "normalize_pod_resource_grants",
        AsyncMock(
            return_value=([SimpleNamespace(resource_id=folder_id)] if folder_id else [])
        ),
    )
    return replace, delete


async def _sync(uow, *, toolsets):
    await sync_memory_folder_grant(
        uow,
        pod_id=uuid4(),
        agent_id=uuid4(),
        toolsets=toolsets,
        ctx=SimpleNamespace(),
        created_by_user_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_a_folder_write_denial_leaves_the_agent_saved_and_the_grant_deferred(
    monkeypatch,
):
    """The case a create-only role hits: no `folder.write`, still gets an agent."""
    denial = DomainError(
        "Missing permission folder.write on document",
        code="INSUFFICIENT_PERMISSION",
        status_code=403,
    )
    _file_service(monkeypatch, AsyncMock(side_effect=denial))
    replace, delete = _grant_spies(monkeypatch, folder_id=uuid4())

    await _sync(_uow(), toolsets=[AgentToolset.MEMORY])

    replace.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failure_that_is_not_a_denial_still_reaches_the_caller(monkeypatch):
    """Swallowing every `DomainError` would hide real breakage behind a warning."""
    _file_service(
        monkeypatch,
        AsyncMock(side_effect=DomainError("Path must point to a folder")),
    )
    _grant_spies(monkeypatch, folder_id=uuid4())

    with pytest.raises(DomainError):
        await _sync(_uow(), toolsets=[AgentToolset.MEMORY])


@pytest.mark.asyncio
async def test_memory_grants_folder_write_on_the_provisioned_folder(monkeypatch):
    folder_id = uuid4()
    _file_service(monkeypatch, AsyncMock())
    replace, delete = _grant_spies(monkeypatch, folder_id=folder_id)

    await _sync(_uow(), toolsets=[AgentToolset.MEMORY])

    delete.assert_not_awaited()
    assert replace.await_args.kwargs["resource_id"] == folder_id
    assert replace.await_args.kwargs["permission_ids"] == ["folder.write"]


@pytest.mark.asyncio
async def test_the_second_agent_on_a_pod_reuses_the_folder(monkeypatch):
    """`/memory` already exists after the first agent; a conflict is the norm."""
    folder_id = uuid4()
    _file_service(monkeypatch, AsyncMock(side_effect=DatastoreConflictError("exists")))
    replace, _ = _grant_spies(monkeypatch, folder_id=folder_id)

    await _sync(_uow(), toolsets=[AgentToolset.MEMORY])

    assert replace.await_args.kwargs["resource_id"] == folder_id


@pytest.mark.asyncio
async def test_memory_off_revokes_without_provisioning_anything(monkeypatch):
    create_folder = _file_service(monkeypatch, AsyncMock())
    replace, delete = _grant_spies(monkeypatch, folder_id=uuid4())

    await _sync(_uow(), toolsets=[AgentToolset.WEB_SEARCH])

    create_folder.assert_not_awaited()
    replace.assert_not_awaited()
    delete.assert_awaited_once()
