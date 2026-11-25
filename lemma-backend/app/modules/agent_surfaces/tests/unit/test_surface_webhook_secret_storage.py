"""A surface's webhook secret: stored in the vault, read from the old column
only for a row the previous release wrote, and moved on the next write."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.crypto import get_secret_cipher
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.surface_webhook_secrets import (
    WEBHOOK_SECRET_PURPOSE,
    reveal_webhook_secret,
    store_webhook_secret,
)
from app.modules.test_support.mappers import configure_test_mappers
from app.modules.test_support.vault_fake import FakeVault
from app.modules.vault.contracts import SecretScope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# Building a model configures the mappers, which a partial graph cannot do.
configure_test_mappers()


def _row(**values) -> AgentSurface:
    return AgentSurface(
        id=uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        **{"webhook_secret_id": None, "webhook_secret": None, **values},
    )


async def test_a_first_secret_is_put_under_the_migrations_scope_and_purpose():
    vault, row = FakeVault(), _row()

    await store_webhook_secret(vault, row, "telegram-secret")

    stored = vault.secrets[row.webhook_secret_id]
    # Bound into the ciphertext: anything else cannot read what 0078 copied.
    assert stored.purpose == WEBHOOK_SECRET_PURPOSE
    assert stored.purpose == "agent_surfaces.surface.webhook_secret"
    assert stored.scope == SecretScope(row.organization_id, row.pod_id)
    assert stored.value == "telegram-secret"
    assert await reveal_webhook_secret(vault, row) == "telegram-secret"


async def test_a_new_secret_replaces_the_stored_one_in_place():
    vault, row = FakeVault(), _row()
    await store_webhook_secret(vault, row, "first")
    secret_id = row.webhook_secret_id

    await store_webhook_secret(vault, row, "second")

    assert row.webhook_secret_id == secret_id
    assert len(vault.secrets) == 1
    assert await reveal_webhook_secret(vault, row) == "second"


async def test_a_row_the_previous_release_wrote_is_read_and_moved_on_write():
    vault = FakeVault()
    row = _row(webhook_secret=get_secret_cipher().encrypt_str("old-release"))

    assert await reveal_webhook_secret(vault, row) == "old-release"

    await store_webhook_secret(vault, row, "minted")

    assert row.webhook_secret is None, "the old column is cleared, not left stale"
    assert row.webhook_secret_id is not None
    assert await reveal_webhook_secret(vault, row) == "minted"


async def test_a_row_with_no_secret_has_none():
    assert await reveal_webhook_secret(FakeVault(), _row()) is None
