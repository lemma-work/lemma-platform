"""Creating a runtime profile keeps what the request asked for.

Agent Host profiles are driven through the real pairing and harness-publish
seam, like the other Agent Host suites, so the harness a profile binds is one a
computer published.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import update
from starlette import status

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.runtime_profiles import (
    DEFAULT_HOST_WAIT_TIMEOUT_SECONDS,
    AgentRuntimeProfile,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostModel
from app.modules.agent.tests.e2e.agent_host_helpers import paired_machine

pytestmark = pytest.mark.e2e


async def _online_machine(authenticated_client, async_client, db_session) -> str:
    machine = await paired_machine(
        SimpleNamespace(owner_client=authenticated_client, async_client=async_client),
        display_name=f"create-e2e-{uuid4().hex[:8]}",
    )
    # Stamped the way the link's heartbeat would: this test runs no host.
    await db_session.execute(
        update(AgentHostModel)
        .where(AgentHostModel.id == machine["host_id"])
        .values(status="ONLINE", last_seen_at=datetime.now(timezone.utc))
    )
    await db_session.commit()
    return str(machine["harness_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "stored"),
    [
        pytest.param(120, 120, id="asked for"),
        pytest.param(None, DEFAULT_HOST_WAIT_TIMEOUT_SECONDS, id="left out"),
    ],
)
async def test_the_wait_timeout_given_at_creation_is_the_one_saved(
    authenticated_client,
    async_client,
    fixed_test_org,
    db_session,
    requested: int | None,
    stored: int,
) -> None:
    harness_id = await _online_machine(authenticated_client, async_client, db_session)
    body: dict[str, object] = {
        "source": "AGENT_HOST",
        "harness_id": harness_id,
        "name": f"Laptop {uuid4().hex[:8]}",
    }
    if requested is not None:
        body["host_wait_timeout_seconds"] = requested

    created = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles", json=body
    )

    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert created.json()["config"]["host_wait_timeout_seconds"] == stored


@pytest.mark.asyncio
async def test_a_stored_profile_keeps_the_id_it_was_built_with(fixed_test_org) -> None:
    """The creator builds the entity with an id; the row must carry that id
    rather than one of the database's own, or the caller holds an id that
    names nothing."""
    organization_id = UUID(fixed_test_org["id"])
    built = AgentRuntimeProfile(
        id=str(uuid7()),
        organization_id=organization_id,
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name=f"Kept id {uuid4().hex[:8]}",
        default_model_name="model-1",
        model_catalog=[{"name": "model-1", "provider_model_name": "model-1"}],
        config={"base_url": "https://api.example.com/v1"},
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        repository = AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher())
        stored = await repository.create(built)
        await uow.commit()
        found = await repository.get_visible_by_id(
            profile_id=built.id, organization_id=organization_id, user_id=uuid4()
        )

    assert stored.id == built.id
    assert found is not None and found.name == built.name


@pytest.mark.asyncio
async def test_a_refused_profile_says_why_without_quoting_pydantic(
    authenticated_client, fixed_test_org
) -> None:
    """Each refusal is a typed error with its own code. A pydantic message,
    which quotes the input it refused, used to reach the client verbatim."""
    created = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Hot {uuid4().hex[:8]}",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "sk-e2e-not-real",
            "model_names": ["model-1"],
            "model_settings": {"temperature": "hot-and-secret"},
        },
    )

    assert created.status_code == status.HTTP_400_BAD_REQUEST, created.text
    body = created.json()
    assert body["code"] == "RUNTIME_PROFILE_INVALID"
    assert "temperature" in body["message"]
    for leaked in ("validation error for", "input_value", "hot-and-secret"):
        assert leaked not in created.text
