"""Whose a machine's secrets are: the person or pod that owns the machine.

A machine keeps two kinds of secret in the vault -- the data key of each login
copy and the environment of each named service -- and both are bound to the
machine's owner, as the vault migration (``0078_vault_cutover``) bound the
ones it copied. The binding is part of the ciphertext, so these must stay
exactly what the migration wrote.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.vault.contracts import SecretScope
from app.modules.workspace.domain.sandbox import SandboxOwnerKind
from app.modules.workspace.infrastructure.models import SandboxModel

LOGIN_BACKUP_KEY_PURPOSE = "workspace.login_backup.key"
MACHINE_SERVICE_ENV_PURPOSE = "workspace.machine_service.env"


class MachineGone(LookupError):
    """The machine a secret would belong to has no row."""


def owner_scope(owner_kind: str, owner_id: UUID) -> SecretScope:
    if owner_kind == SandboxOwnerKind.POD.value:
        return SecretScope(None, pod_id=owner_id)
    return SecretScope(None, user_id=owner_id)


async def machine_secret_scope(session: AsyncSession, machine_id: UUID) -> SecretScope:
    """The scope of ``machine_id``'s secrets, from its row."""
    owner = (
        await session.execute(
            select(SandboxModel.owner_kind, SandboxModel.owner_id).where(
                SandboxModel.id == machine_id
            )
        )
    ).one_or_none()
    if owner is None:
        raise MachineGone(str(machine_id))
    return owner_scope(owner.owner_kind, owner.owner_id)


__all__ = [
    "LOGIN_BACKUP_KEY_PURPOSE",
    "MACHINE_SERVICE_ENV_PURPOSE",
    "MachineGone",
    "machine_secret_scope",
    "owner_scope",
]
