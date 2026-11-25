"""A machine's secrets are vault secrets, bound to whoever owns the machine.

Two kinds: the data key of each login copy, and the caller's environment of
each named service. Each is written to the vault only, bound to the machine's
owner -- the person, or the pod -- as the vault migration bound the ones it
copied, and goes with its row. A row a process on the previous release wrote
mid-rollout, its secret still in the old Fernet column, is still read.

Nothing here starts a machine: the rows, the vault and the object store are
real, the machine's shell is a function.
"""

from __future__ import annotations

import base64
import json
import os
from uuid import UUID, uuid4, uuid7

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update

from app.core.crypto.factory import get_secret_cipher
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.vault.infrastructure.models import VaultSecret
from app.modules.workspace.infrastructure.machine_models import (
    MachineLoginBackupModel,
    MachineServiceModel,
)
from app.modules.workspace.infrastructure.models import SandboxModel
from app.modules.workspace.services import login_backup
from app.modules.workspace.services.login_backup import LoginBackups
from app.modules.workspace.services.machine_services import MachineServices
from app.modules.workspace.services.machine_shell import ShellOutcome

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
def uow_factory(db_manager) -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(db_manager.session_factory)


async def _machine(uow_factory, owner_kind: str) -> tuple[UUID, UUID]:
    """A machine row owned by a new person or pod: its id and its owner's."""
    owner_id = uuid4()
    machine_id = uuid7()
    async with uow_factory() as uow:
        uow.session.add(
            SandboxModel(
                id=machine_id,
                kind="workspace",
                owner_kind=owner_kind,
                owner_id=owner_id,
                slug=f"m-{machine_id.hex[:8]}",
                display_name="Machine",
                profile_name="default",
                profile_digest="sha256:" + "0" * 64,
            )
        )
        await uow.commit()
    return machine_id, owner_id


async def _secret(uow_factory, secret_id: UUID) -> VaultSecret | None:
    async with uow_factory() as uow:
        return await uow.session.get(VaultSecret, secret_id)


def _owner(secret: VaultSecret) -> tuple[UUID | None, UUID | None, UUID | None]:
    return secret.organization_id, secret.pod_id, secret.user_id


async def _ok(machine_id, *, acting_user, command, timeout_seconds) -> ShellOutcome:
    return ShellOutcome(0, "", "")


@pytest.fixture
def restored() -> list[bytes]:
    """What a restore would write onto the machine."""
    return []


def _backups(uow_factory, restored: list[bytes]) -> LoginBackups:
    async def write(sandbox_id, data: bytes) -> None:
        restored.append(data)

    return LoginBackups(uow_factory, shell=_ok, write_staged=write)


@pytest.mark.parametrize("owner_kind", ["user", "pod"])
async def test_a_login_backups_key_is_a_vault_secret_of_the_machines_owner(
    uow_factory, restored, owner_kind
) -> None:
    machine_id, owner_id = await _machine(uow_factory, owner_kind)
    backups = _backups(uow_factory, restored)

    backup = await backups._store_backup(machine_id, b"archive", reason="test")
    assert backup is not None

    async with uow_factory() as uow:
        row = await uow.session.get(MachineLoginBackupModel, backup.id)
    assert row is not None and row.wrapped_key is None
    assert row.key_secret_id is not None
    secret = await _secret(uow_factory, row.key_secret_id)
    assert secret is not None
    assert secret.purpose == "workspace.login_backup.key"
    if owner_kind == "pod":
        assert _owner(secret) == (None, owner_id, None)
    else:
        assert _owner(secret) == (None, None, owner_id)

    assert await backups.restore(machine_id, acting_user=uuid4())
    assert restored == [b"archive"]

    await backups.forget(machine_id)
    assert await _secret(uow_factory, row.key_secret_id) is None, "gone with it"


async def test_a_login_copy_taken_before_the_vault_still_restores(
    uow_factory, restored
) -> None:
    machine_id, _ = await _machine(uow_factory, "user")
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    sealed = nonce + AESGCM(key).encrypt(nonce, b"old archive", machine_id.bytes)
    object_key = f"{machine_id}/{uuid7()}.bin"
    await login_backup._store().put_async(object_key, sealed)
    async with uow_factory() as uow:
        uow.session.add(
            MachineLoginBackupModel(
                sandbox_id=machine_id,
                object_key=object_key,
                wrapped_key=get_secret_cipher().encrypt_str(
                    base64.b64encode(key).decode()
                ),
                size_bytes=len(sealed),
                sha256="0" * 64,
                reason="test",
            )
        )
        await uow.commit()

    assert await _backups(uow_factory, restored).restore(
        machine_id, acting_user=uuid4()
    )
    assert restored == [b"old archive"]


async def _service_row(uow_factory, machine_id: UUID) -> MachineServiceModel:
    async with uow_factory() as uow:
        return (
            await uow.session.scalars(
                select(MachineServiceModel).where(
                    MachineServiceModel.sandbox_id == machine_id
                )
            )
        ).one()


async def _record(services: MachineServices, machine_id: UUID, env) -> None:
    await services.record(
        machine_id,
        name="web",
        command="npm run dev",
        cwd="/home/user",
        env=env,
        claims=None,
        started_by=None,
        restart_on_resume=True,
        process_id=str(uuid4()),
    )


async def _env(services: MachineServices, machine_id: UUID) -> dict[str, str]:
    [service] = await services.running(machine_id)
    return service.revealed_env()


@pytest.mark.parametrize("owner_kind", ["user", "pod"])
async def test_a_services_variables_are_a_vault_secret_replaced_with_it(
    uow_factory, owner_kind
) -> None:
    machine_id, owner_id = await _machine(uow_factory, owner_kind)
    services = MachineServices(uow_factory)

    await _record(services, machine_id, {"API_KEY": "s3cret-value"})

    row = await _service_row(uow_factory, machine_id)
    assert row.env_ciphertext is None and row.env_secret_id is not None
    first = await _secret(uow_factory, row.env_secret_id)
    assert first is not None
    assert first.purpose == "workspace.machine_service.env"
    assert b"s3cret-value" not in first.ciphertext
    if owner_kind == "pod":
        assert _owner(first) == (None, owner_id, None)
    else:
        assert _owner(first) == (None, None, owner_id)
    assert await _env(services, machine_id) == {"API_KEY": "s3cret-value"}

    # Recorded again under its name: the earlier secret goes.
    await _record(services, machine_id, {"API_KEY": "rotated"})
    again = await _service_row(uow_factory, machine_id)
    assert again.env_secret_id not in (None, first.id)
    assert await _secret(uow_factory, first.id) is None
    assert await _env(services, machine_id) == {"API_KEY": "rotated"}

    # And with no variables, none is kept.
    await _record(services, machine_id, {})
    assert (await _service_row(uow_factory, machine_id)).env_secret_id is None
    assert await _secret(uow_factory, again.env_secret_id) is None
    assert await _env(services, machine_id) == {}


async def test_a_service_recorded_before_the_vault_still_starts_with_its_variables(
    uow_factory,
) -> None:
    machine_id, _ = await _machine(uow_factory, "user")
    services = MachineServices(uow_factory)
    await _record(services, machine_id, {})
    async with uow_factory() as uow:
        await uow.session.execute(
            update(MachineServiceModel)
            .where(MachineServiceModel.sandbox_id == machine_id)
            .values(
                env_ciphertext=get_secret_cipher().encrypt_str(
                    json.dumps({"API_KEY": "from-the-old-release"})
                )
            )
        )
        await uow.commit()

    assert await _env(services, machine_id) == {"API_KEY": "from-the-old-release"}

    # The next record moves it into the vault.
    await _record(services, machine_id, {"API_KEY": "from-the-old-release"})
    row = await _service_row(uow_factory, machine_id)
    assert row.env_ciphertext is None and row.env_secret_id is not None
    assert await _env(services, machine_id) == {"API_KEY": "from-the-old-release"}
