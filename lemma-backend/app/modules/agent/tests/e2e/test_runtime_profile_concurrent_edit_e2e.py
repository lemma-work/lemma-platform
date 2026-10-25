"""Two people editing one model at once: the second write is refused, not merged.

An edit reads the whole profile, may spend seconds talking to the provider
with its connection handed back, and then writes every column it read. Without
a version check the slower edit put back everything the faster one changed --
a rotated key restored, an archived profile made active again. Each test makes
its own organization, so nothing here touches another test's profiles.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.errors import RuntimeProfileConflictError
from app.modules.agent.domain.runtime_profiles import AgentRuntimeProfile
from app.modules.agent.infrastructure.agent_host.repository import AgentHostRepository
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.application.agents.edit_runtime_profile import (
    AgentRuntimeProfileEditor,
)
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)

pytestmark = pytest.mark.e2e

# Nothing listens on the discard port, so discovery finds no models and the
# names given at creation stand.
_NOTHING_LISTENING = "http://127.0.0.1:9/v1"

Edit = Callable[[AgentRuntimeProfileEditor, str, UUID], Awaitable[AgentRuntimeProfile]]


async def _provider(client: AsyncClient) -> tuple[UUID, dict[str, object]]:
    created_org = await client.post(
        "/organizations", json={"name": f"Concurrent edit {uuid4().hex[:8]}"}
    )
    assert created_org.status_code == status.HTTP_201_CREATED, created_org.text
    org_id = UUID(created_org.json()["id"])
    created = await client.post(
        f"/organizations/{org_id}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Provider {uuid4().hex[:8]}",
            "base_url": _NOTHING_LISTENING,
            "api_key": "sk-e2e-not-real",
            "model_names": ["model-1"],
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return org_id, created.json()


def _editor(uow) -> AgentRuntimeProfileEditor:
    return AgentRuntimeProfileEditor(
        AgentRuntimeProfileService(
            AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher()),
            AgentHostRepository(uow),
        )
    )


async def _rename(editor: AgentRuntimeProfileEditor, profile_id: str, org_id: UUID):
    return await editor.update_openai_compatible_profile(
        profile_id=profile_id,
        organization_id=org_id,
        user_id=uuid4(),
        name=f"Renamed {uuid4().hex[:8]}",
    )


async def _rotate_key(editor: AgentRuntimeProfileEditor, profile_id: str, org_id: UUID):
    return await editor.update_openai_compatible_profile(
        profile_id=profile_id,
        organization_id=org_id,
        user_id=uuid4(),
        api_key="sk-e2e-rotated",
    )


async def _archive(editor: AgentRuntimeProfileEditor, profile_id: str, org_id: UUID):
    return await editor.archive_profile(
        profile_id=profile_id, organization_id=org_id, user_id=uuid4()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second",
    [
        pytest.param(_rotate_key, id="key rotation"),
        pytest.param(_archive, id="archive"),
    ],
)
async def test_the_second_of_two_overlapping_edits_gets_a_conflict(
    authenticated_client: AsyncClient,
    second: Edit,
) -> None:
    org_id, profile = await _provider(authenticated_client)
    profile_id = str(profile["id"])
    first_written = asyncio.Event()
    second_read = asyncio.Event()

    async def first_edit_held_open() -> AgentRuntimeProfile:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            renamed = await _rename(_editor(uow), profile_id, org_id)
            first_written.set()
            # Hold the row until the second edit has read it and is writing.
            await second_read.wait()
            await asyncio.sleep(0.3)
            await uow.commit()
            return renamed

    async def second_edit() -> AgentRuntimeProfile:
        await first_written.wait()
        async with create_uow_from_session_maker(async_session_maker) as uow:
            editor = _editor(uow)
            # Reads the version the first edit has not committed past yet,
            # then blocks on the row until it does.
            second_read.set()
            return await second(editor, profile_id, org_id)

    holder = asyncio.create_task(first_edit_held_open())
    with pytest.raises(RuntimeProfileConflictError) as refused:
        await second_edit()
    renamed = await holder

    assert refused.value.status_code == status.HTTP_409_CONFLICT
    listing = await authenticated_client.get(
        f"/organizations/{org_id}/agent-runtime/profiles",
        params={"include_disabled": True},
    )
    stored = next(item for item in listing.json()["items"] if item["id"] == profile_id)
    # The first edit stands whole; nothing of the second was merged into it.
    assert stored["name"] == renamed.name
    assert stored["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_an_edit_built_on_a_read_another_edit_overtook_is_refused(
    authenticated_client: AsyncClient,
) -> None:
    """Not overlapping at all: the first edit commits long before the second
    writes, but the second still holds the row as it was before."""
    org_id, profile = await _provider(authenticated_client)
    profile_id = str(profile["id"])

    async with create_uow_from_session_maker(async_session_maker) as stale_uow:
        repository = AgentRuntimeProfileRepository(
            stale_uow, encryption=get_secret_cipher()
        )
        read_before = await repository.get_visible_by_id(
            profile_id=profile_id, organization_id=org_id, user_id=uuid4()
        )
        assert read_before is not None

        async with create_uow_from_session_maker(async_session_maker) as uow:
            await _rotate_key(_editor(uow), profile_id, org_id)
            await uow.commit()

        with pytest.raises(RuntimeProfileConflictError):
            await repository.update(read_before.with_changes(name="Stale rename"))

    # And over HTTP an ordinary edit, on a fresh read, still goes through.
    patched = await authenticated_client.patch(
        f"/organizations/{org_id}/agent-runtime/profiles/{profile_id}",
        json={"source": "OPENAI_COMPATIBLE", "name": "Fresh rename"},
    )
    assert patched.status_code == status.HTTP_200_OK, patched.text
    assert patched.json()["name"] == "Fresh rename"
