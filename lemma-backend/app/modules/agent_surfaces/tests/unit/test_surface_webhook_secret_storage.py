"""A surface's webhook secret: stored in the vault, revealed by the row.

The entity carries the vault id and, briefly, a secret it has just minted; the
repository is the only thing that turns one into the other. These drive it
against the fake vault on a stub session, so they stay in the unit lane and
still exercise the vault's refusals -- a wrong scope is a mismatch, not a value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.infrastructure.surface_webhook_secrets import (
    WEBHOOK_SECRET_PURPOSE,
    reveal_webhook_secret,
    store_webhook_secret,
)
from app.modules.test_support.mappers import configure_test_mappers
from app.modules.test_support.vault_fake import FakeVault
from app.modules.vault.contracts import SecretScope, SecretScopeMismatch

configure_test_mappers()

pytestmark = pytest.mark.asyncio


def _row(**overrides) -> AgentSurface:
    now = datetime.now(UTC)
    pod_id = uuid4()
    fields = {
        "id": uuid4(),
        "created_at": now,
        "updated_at": now,
        "organization_id": uuid4(),
        "pod_id": pod_id,
        "name": "telegram",
        "agent_id": pod_id,
        "surface_type": "TELEGRAM",
        "credential_mode": "CUSTOM",
        "config": {},
        "account_id": uuid4(),
        "status": "ACTIVE",
        "webhook_secret_id": None,
    }
    fields.update(overrides)
    return AgentSurface(**fields)


class _Session:
    """Only what the repository's secret paths touch: `get` and `flush`."""

    def __init__(self, *rows: AgentSurface) -> None:
        self._rows = {row.id: row for row in rows}
        self.gets = 0

    async def get(self, _model, id: UUID):
        self.gets += 1
        return self._rows.get(id)

    async def flush(self) -> None:
        return None


class _Uow:
    def __init__(self, *rows: AgentSurface) -> None:
        self.session = _Session(*rows)

    def collect_events(self, _events) -> None:
        return None


def _entity_for(row: AgentSurface) -> AgentSurfaceEntity:
    return row.to_entity()


async def test_a_first_secret_is_put_under_the_rows_org_and_pod():
    vault = FakeVault()
    row = _row()

    await store_webhook_secret(vault, row, "minted")

    assert row.webhook_secret_id is not None
    secret = vault.secrets[row.webhook_secret_id]
    assert secret.scope == SecretScope(row.organization_id, pod_id=row.pod_id)
    assert secret.purpose == WEBHOOK_SECRET_PURPOSE
    assert secret.owner_table == "agent_surfaces"
    assert secret.value == "minted"


async def test_a_new_secret_replaces_the_stored_one_in_place():
    """Same id, next version: the row is not repointed, the old value is gone."""
    vault = FakeVault()
    row = _row()
    await store_webhook_secret(vault, row, "first")
    first_id = row.webhook_secret_id

    await store_webhook_secret(vault, row, "second")

    assert row.webhook_secret_id == first_id
    assert vault.secrets[first_id].version == 2
    assert await reveal_webhook_secret(vault, row) == "second"


async def test_a_row_pointed_at_another_orgs_secret_does_not_reveal_it():
    """The scope comes from the row, so a repointed id is a refusal, not a value."""
    vault = FakeVault()
    owner = _row()
    await store_webhook_secret(vault, owner, "theirs")
    intruder = _row(webhook_secret_id=owner.webhook_secret_id)

    with pytest.raises(SecretScopeMismatch):
        await reveal_webhook_secret(vault, intruder)


async def test_the_repository_reveals_a_surfaces_stored_secret():
    vault = FakeVault()
    row = _row()
    await store_webhook_secret(vault, row, "stored")
    repository = SurfaceRepository(_Uow(row), vault=vault)

    assert await repository.reveal_webhook_secret(_entity_for(row)) == "stored"


async def test_a_surface_without_a_secret_reveals_none_without_a_read():
    """Most surfaces have no secret; asking about one costs nothing."""
    row = _row()
    uow = _Uow(row)
    repository = SurfaceRepository(uow, vault=FakeVault())

    assert await repository.reveal_webhook_secret(_entity_for(row)) is None
    assert uow.session.gets == 0


async def test_update_stores_a_freshly_minted_secret_and_reports_its_id():
    vault = FakeVault()
    row = _row()
    repository = SurfaceRepository(_Uow(row), vault=vault)
    entity = _entity_for(row)
    entity.configure_webhook_secret(secret="minted")

    updated = await repository.update(entity)

    assert updated.webhook_secret_id == row.webhook_secret_id is not None
    assert vault.value_of(row.webhook_secret_id) == "minted"


async def test_an_ordinary_update_leaves_the_stored_secret_alone():
    """A read never fills the plaintext in, so None means "unchanged".

    Treating it as "clear it" would wipe every bot's secret on the first edit
    after this change -- and every webhook after that would be a 503.
    """
    vault = FakeVault()
    row = _row()
    await store_webhook_secret(vault, row, "stored")
    stored_id = row.webhook_secret_id
    repository = SurfaceRepository(_Uow(row), vault=vault)
    entity = _entity_for(row)
    entity.toggle_active(False)

    updated = await repository.update(entity)

    assert updated.webhook_secret_id == stored_id
    assert vault.secrets[stored_id].version == 1
    assert vault.value_of(stored_id) == "stored"


async def test_an_entity_created_for_telegram_starts_without_a_secret():
    entity = AgentSurfaceEntity(
        pod_id=uuid4(),
        name="telegram",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
    )
    assert not entity.has_webhook_secret
    entity.configure_webhook_secret(secret="minted")
    assert entity.has_webhook_secret
