"""What revoking a phone-bound identity actually writes."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (
    VerifiedSurfaceIdentityRepository,
)

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement) -> None:
        self.statements.append(
            str(
                statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
        )


async def _revoke(phone: str | None) -> str:
    session = _Session()
    repository = VerifiedSurfaceIdentityRepository(SimpleNamespace(session=session))
    await repository.revoke_phone_bound_except(uuid4(), phone)
    return session.statements[0]


@pytest.mark.asyncio
async def test_a_changed_number_revokes_only_identities_bound_to_another_number():
    statement = await _revoke("+15550001111")

    assert "SET revoked_at" in statement
    assert "verified_phone IS NOT NULL" in statement
    assert "verified_phone != '+15550001111'" in statement


@pytest.mark.asyncio
async def test_no_verified_number_revokes_every_phone_bound_identity():
    statement = await _revoke(None)

    assert "verified_phone IS NOT NULL" in statement
    # The exemption collapses to a SQL literal rather than a Python bool.
    assert "verified_phone !=" not in statement
    assert "verified_phone =" not in statement
