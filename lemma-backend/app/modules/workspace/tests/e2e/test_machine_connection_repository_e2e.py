"""The rows behind connected machines, against Postgres: what a code that
lapses gives back, which link's report is the machine's, and what a revoked
machine's row is renamed to.

Row locks, advisory locks, partial unique indexes and ``UPDATE ... RETURNING``
are the subject here, so these run on the real database rather than a double.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts.compute_link import MachineReport
from app.modules.workspace.domain.machine_connection import (
    ConnectionNameTaken,
    PairingNotFound,
    secret_hash,
)
from app.modules.workspace.domain.sandbox import (
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.machine_connection_repository import (
    MachineConnectionRepository,
    retired_slug,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.services.machine_link.store import report_columns

pytestmark = pytest.mark.e2e

_TTL = timedelta(minutes=10)
_T0 = datetime.now(timezone.utc)


@pytest.fixture
def uow_factory(db_manager) -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(db_manager.session_factory)


async def _a_person(uow) -> UUID:
    user_id = uuid4()
    await uow.session.execute(
        text(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) "
            "VALUES (:id, :email, true, true, false, false, now(), now())"
        ),
        {"id": user_id, "email": f"machines-{user_id.hex[:12]}@example.com"},
    )
    return user_id


async def _approved(
    repository: MachineConnectionRepository,
    user_id: UUID,
    name: str,
    *,
    at: datetime = _T0,
    limit: int | None = None,
):
    code = f"code-{uuid4().hex}"
    await repository.create_pairing(
        code_hash=secret_hash(code),
        claim_key_hash=secret_hash(f"key-{code}"),
        display_name="Laptop",
        mode="host",
        platform="linux",
        ttl=_TTL,
        now=at,
    )
    connection = await repository.approve_pairing(
        code_hash=secret_hash(code),
        user_id=user_id,
        name=name,
        pod_id=None,
        limit=limit,
        now=at,
    )
    return code, connection


async def _claimed(repository, code: str, *, at: datetime = _T0) -> str:
    secret = f"secret-{uuid4().hex}"
    await repository.claim_pairing(
        code_hash=secret_hash(code),
        claim_key_hash=secret_hash(f"key-{code}"),
        secret_hash=secret_hash(secret),
        installation_id="install-1",
        now=at,
    )
    return secret


def _report(runtime_port: int) -> dict[str, object]:
    return report_columns(
        MachineReport(
            mode="host", platform="linux", home="/home/me", runtime_port=runtime_port
        )
    )


@pytest.mark.asyncio
async def test_an_approved_code_never_claimed_gives_back_its_name_and_slot(
    uow_factory,
) -> None:
    async with uow_factory() as uow:
        repository = MachineConnectionRepository(uow)
        user_id = await _a_person(uow)
        kept_code, kept = await _approved(repository, user_id, "desk", limit=2)
        await _claimed(repository, kept_code)
        _, lapsed = await _approved(repository, user_id, "laptop", limit=2)

        # The next code anyone asks for, after the approved one lapsed.
        later = _T0 + _TTL + timedelta(seconds=1)
        # Two live connections would be the limit; the lapsed one is not one.
        await _approved(repository, user_id, "spare", at=later, limit=2)
        uow.session.expire_all()

        assert (await repository.get(lapsed.id)).revoked_at == later
        machine = await SandboxRepository(uow).get(lapsed.id)
        assert machine.desired_state is SandboxDesiredState.DELETED
        assert machine.slug == retired_slug(lapsed.id)
        # One that completed its claim is the person's, untouched.
        assert (await repository.get(kept.id)).revoked_at is None
        # And the name and the slot are free again.
        _, again = await _approved(repository, user_id, "laptop", at=later, limit=3)
        assert again.name == "laptop"


@pytest.mark.asyncio
async def test_a_beat_from_a_superseded_link_writes_nothing(uow_factory) -> None:
    async with uow_factory() as uow:
        repository = MachineConnectionRepository(uow)
        user_id = await _a_person(uow)
        code, connection = await _approved(repository, user_id, "laptop")
        secret = await _claimed(repository, code)
        opened = []
        for port in (18080, 18081):
            opened.append(
                await repository.open_link(
                    secret_hash=secret_hash(secret),
                    installation_id="install-1",
                    report=_report(port),
                )
            )
        (_, old), (_, new) = opened
        assert new > old

        assert (
            await repository.heartbeat(
                connection.id, generation=old, report=_report(19999)
            )
            == new
        )
        uow.session.expire_all()
        assert (await repository.get(connection.id)).capabilities[
            "runtime_port"
        ] == 18081
        assert (
            await repository.heartbeat(
                connection.id, generation=new, report=_report(18082)
            )
            == new
        )
        uow.session.expire_all()
        assert (await repository.get(connection.id)).capabilities[
            "runtime_port"
        ] == 18082

        await repository.revoke(connection.id, user_id=user_id)
        assert (
            await repository.heartbeat(
                connection.id, generation=new, report=_report(18082)
            )
            is None
        )


@pytest.mark.asyncio
async def test_revoking_a_machine_with_the_longest_name_fits_its_row(
    uow_factory,
) -> None:
    async with uow_factory() as uow:
        repository = MachineConnectionRepository(uow)
        user_id = await _a_person(uow)
        _, connection = await _approved(repository, user_id, "a" * 64)

        await repository.revoke(connection.id, user_id=user_id)
        uow.session.expire_all()

        machine = await SandboxRepository(uow).get(connection.id)
        assert machine.slug == retired_slug(connection.id)


@pytest.mark.asyncio
async def test_a_name_another_machine_of_theirs_holds_is_taken(uow_factory) -> None:
    async with uow_factory() as uow:
        repository = MachineConnectionRepository(uow)
        user_id = await _a_person(uow)
        await SandboxRepository(uow).ensure_row(
            sandbox_id=uuid4(),
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user_id,
            slug="host-abc",
        )

        with pytest.raises(ConnectionNameTaken):
            await _approved(repository, user_id, "host-abc")


@pytest.mark.asyncio
async def test_a_code_is_spent_at_its_expiry(uow_factory) -> None:
    async with uow_factory() as uow:
        repository = MachineConnectionRepository(uow)
        user_id = await _a_person(uow)
        code = f"code-{uuid4().hex}"
        await repository.create_pairing(
            code_hash=secret_hash(code),
            claim_key_hash=secret_hash("key"),
            display_name="Laptop",
            mode="host",
            platform="linux",
            ttl=_TTL,
            now=_T0,
        )

        with pytest.raises(PairingNotFound):
            await repository.approve_pairing(
                code_hash=secret_hash(code),
                user_id=user_id,
                name="laptop",
                pod_id=None,
                limit=None,
                now=_T0 + _TTL,
            )
