"""A runtime profile's credentials and header values live in the vault.

Against the real database: what reaches the row, what reaches the vault, that
a row the previous release wrote during the rollout still reads, and that its
next write moves it into the vault and empties the old column.
"""

from __future__ import annotations

from uuid import UUID, uuid4, uuid7

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    reveal_credentials,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.repositories.runtime_profile_repository import (
    SECRETS_PURPOSE,
    put_profile_secrets,
)
from app.modules.agent.infrastructure.runtime_models import AgentRuntimeProfileModel
from app.modules.vault.contracts import SecretScope, vault_for
from app.modules.vault.infrastructure.models import VaultSecret

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

#: Canaries: plain markers to look for, not credentials.
_KEY = "canary-provider-value"
_HEADER = "canary-header-value"


def _profile(organization_id: UUID, **overrides: object) -> AgentRuntimeProfile:
    fields: dict[str, object] = {
        "id": str(uuid7()),
        "organization_id": organization_id,
        "scope": RuntimeProfileScope.ORGANIZATION,
        "kind": RuntimeProfileKind.MODEL_PROVIDER,
        "protocol": RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        "name": f"Vaulted {uuid4().hex[:8]}",
        "default_model_name": "model-1",
        "model_catalog": [{"name": "model-1", "provider_model_name": "model-1"}],
        "config": {
            "base_url": "https://api.example.com/v1",
            "headers": {"Authorization": _HEADER},
        },
        "credentials": ApiKeyRuntimeCredentials(api_key=SecretStr(_KEY)),
    }
    return AgentRuntimeProfile(**{**fields, **overrides})


async def _row(db_session, profile_id: str) -> AgentRuntimeProfileModel:
    await db_session.rollback()
    row = await db_session.scalar(
        select(AgentRuntimeProfileModel).where(
            AgentRuntimeProfileModel.id == UUID(profile_id)
        )
    )
    assert row is not None
    return row


async def _read(organization_id: UUID, profile_id: str) -> AgentRuntimeProfile:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found = await AgentRuntimeProfileRepository(uow).get_visible_by_id(
            profile_id=profile_id, organization_id=organization_id, user_id=uuid4()
        )
    assert found is not None
    return found


async def _update(profile: AgentRuntimeProfile) -> AgentRuntimeProfile:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        updated = await AgentRuntimeProfileRepository(uow).update(profile)
        await uow.commit()
    return updated


async def test_a_new_profile_keeps_its_key_and_headers_only_in_the_vault(
    fixed_test_org, db_session
) -> None:
    organization_id = UUID(fixed_test_org["id"])
    built = _profile(organization_id)

    async with create_uow_from_session_maker(async_session_maker) as uow:
        created = await AgentRuntimeProfileRepository(uow).create(built)
        await uow.commit()

    row = await _row(db_session, built.id)
    assert row.credentials is None
    assert "headers" not in row.config
    assert row.secrets_secret_id is not None
    stored = (
        await vault_for(db_session).reveal(
            row.secrets_secret_id,
            expect=SecretScope(organization_id=organization_id),
            purpose=SECRETS_PURPOSE,
        )
    ).json()
    assert stored == {
        "credentials": {"api_key": _KEY},
        "headers": {"Authorization": _HEADER},
    }

    for profile in (created, await _read(organization_id, built.id)):
        assert reveal_credentials(profile.credentials) == {"api_key": _KEY}
        assert profile.config.headers == {"Authorization": _HEADER}  # type: ignore[union-attr]
        public = str(profile.public_dict())
        assert _KEY not in public and _HEADER not in public


async def test_a_row_the_previous_release_wrote_reads_and_moves_on_its_next_write(
    fixed_test_org, db_session
) -> None:
    organization_id = UUID(fixed_test_org["id"])
    profile_id = uuid7()
    db_session.add(
        AgentRuntimeProfileModel(
            id=profile_id,
            organization_id=organization_id,
            scope="ORGANIZATION",
            kind="MODEL_PROVIDER",
            runtime_type="MODEL_PROVIDER",
            protocol="OPENAI_COMPATIBLE",
            name=f"Legacy {uuid4().hex[:8]}",
            default_model_name="model-1",
            model_catalog=[{"name": "model-1", "provider_model_name": "model-1"}],
            config={
                "base_url": "https://api.example.com/v1",
                "headers": {"Authorization": _HEADER},
                "model_settings": {},
            },
            credentials=get_secret_cipher().encrypt_json({"api_key": _KEY}),
            status="ACTIVE",
            profile_metadata={},
        )
    )
    await db_session.commit()

    legacy = await _read(organization_id, str(profile_id))
    assert reveal_credentials(legacy.credentials) == {"api_key": _KEY}
    assert legacy.config.headers == {"Authorization": _HEADER}  # type: ignore[union-attr]

    await _update(legacy.with_changes(description="moved"))

    row = await _row(db_session, str(profile_id))
    assert row.credentials is None
    assert "headers" not in row.config
    assert row.secrets_secret_id is not None
    moved = await _read(organization_id, str(profile_id))
    assert reveal_credentials(moved.credentials) == {"api_key": _KEY}
    assert moved.config.headers == {"Authorization": _HEADER}  # type: ignore[union-attr]


async def test_headers_still_in_config_win_over_the_vaults_copy(
    fixed_test_org, db_session
) -> None:
    """The migration leaves a copy of the headers in ``config``, and the
    previous release may rewrite them there; this release never writes them, so
    while they are there they are the newest."""
    organization_id = UUID(fixed_test_org["id"])
    profile_id = uuid7()
    secret_id = await put_profile_secrets(
        vault_for(db_session),
        organization_id=organization_id,
        credentials={"api_key": _KEY},
        headers={"Authorization": "Bearer stale"},
    )
    db_session.add(
        AgentRuntimeProfileModel(
            id=profile_id,
            organization_id=organization_id,
            scope="ORGANIZATION",
            kind="MODEL_PROVIDER",
            runtime_type="MODEL_PROVIDER",
            protocol="OPENAI_COMPATIBLE",
            name=f"Config wins {uuid4().hex[:8]}",
            default_model_name="model-1",
            model_catalog=[{"name": "model-1", "provider_model_name": "model-1"}],
            config={
                "base_url": "https://api.example.com/v1",
                "headers": {"Authorization": _HEADER},
            },
            secrets_secret_id=secret_id,
            status="ACTIVE",
            profile_metadata={},
        )
    )
    await db_session.commit()

    read = await _read(organization_id, str(profile_id))
    assert read.config.headers == {"Authorization": _HEADER}  # type: ignore[union-attr]

    await _update(read.with_changes(description="rewritten"))

    row = await _row(db_session, str(profile_id))
    assert "headers" not in row.config
    assert row.secrets_secret_id == secret_id
    again = await _read(organization_id, str(profile_id))
    assert again.config.headers == {"Authorization": _HEADER}  # type: ignore[union-attr]


async def test_clearing_the_key_and_headers_deletes_the_secret(
    fixed_test_org, db_session
) -> None:
    organization_id = UUID(fixed_test_org["id"])
    built = _profile(organization_id)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        created = await AgentRuntimeProfileRepository(uow).create(built)
        await uow.commit()
    secret_id = (await _row(db_session, built.id)).secrets_secret_id

    await _update(
        created.with_changes(
            credentials=None,
            config={"base_url": "https://api.example.com/v1", "headers": {}},
        )
    )

    row = await _row(db_session, built.id)
    assert row.secrets_secret_id is None
    assert await db_session.get(VaultSecret, secret_id) is None
    cleared = await _read(organization_id, built.id)
    assert cleared.credentials is None
    assert cleared.config.headers == {}  # type: ignore[union-attr]
