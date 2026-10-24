"""Encrypted, off-machine copies of a browser's logins, and putting them back.

A machine's disk can be lost or replaced: a confirmed `home` reset, a move to
a bigger size, a provider that deleted a sandbox it should have paused. The
login-bearing parts of the browser profile are copied off the machine after
clean closes (at most once per fifteen minutes) and daily, the last three
kept, and restored onto any new disk -- so a person signs in to a site once.

Only the parts that carry a login: cookies, saved passwords, local storage,
IndexedDB, preferences and the profile's own key state. Caches and history
stay behind; they are most of the bytes and none of the value.

The copy is AES-256-GCM ciphertext in object storage under a fresh data key,
and the data key is stored on the row under the deployment's secret cipher.
A bucket alone opens nothing. Chrome on Linux runs with the basic password
store, whose cookie key is the same on every machine, which is what makes a
copy taken on one disk usable on another -- the survival matrix holds that.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.object_storage import build_object_store, local_object_storage_path
from app.modules.workspace.infrastructure.machine_models import (
    MachineLoginBackupModel,
)
from app.modules.workspace.services.machine_shell import run_on_machine
from sandbox_runtime.paths import BROWSER_PROFILE_ROOT, sandbox_command

logger = get_logger(__name__)

#: Relative to `BROWSER_PROFILE_ROOT`. Missing ones are skipped: a profile that
#: has never signed in anywhere has no `Login Data` yet.
LOGIN_PARTS = (
    "profile/Local State",
    "profile/Default/Cookies",
    "profile/Default/Cookies-journal",
    "profile/Default/Network",
    "profile/Default/Login Data",
    "profile/Default/Local Storage",
    "profile/Default/IndexedDB",
    "profile/Default/Preferences",
    "signed-in.json",
)
KEEP = 3
#: The fewest minutes between two clean-close copies of one machine.
CLEAN_CLOSE_INTERVAL = timedelta(minutes=15)
DAILY_INTERVAL = timedelta(days=1)
_STAGE = "/tmp/lemma-login-backup.tgz"
_RESTORE = "/tmp/lemma-login-restore.tgz"
_PREFIX = "machine-login-backups"


@dataclass(frozen=True, slots=True)
class LoginBackup:
    id: UUID
    sandbox_id: UUID
    created_at: datetime
    size_bytes: int
    reason: str


def _store():
    return build_object_store(
        local_prefix=local_object_storage_path(_PREFIX), remote_prefix=_PREFIX
    )


def _cipher():
    from app.core.crypto.factory import get_secret_cipher

    return get_secret_cipher()


def close_browser_command() -> str:
    """A clean close: agent-browser copies the profile back only on one."""
    return f"{shlex.quote(sandbox_command('agent-browser'))} close --all >/dev/null 2>&1 || true"


def _tar_command() -> str:
    parts = " ".join(shlex.quote(part) for part in LOGIN_PARTS)
    root = shlex.quote(BROWSER_PROFILE_ROOT)
    return (
        f"cd {root} 2>/dev/null || exit 3; "
        f'present=\'\'; for p in {parts}; do [ -e "$p" ] && present="$present\n$p"; done; '
        '[ -n "$present" ] || exit 3; '
        f'printf "%b" "$present" | sed "/^$/d" | tar czf {_STAGE} -T - 2>/dev/null; '
        f"chmod 600 {_STAGE}"
    )


class LoginBackups:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def latest(self, sandbox_id: UUID) -> LoginBackup | None:
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(MachineLoginBackupModel)
                .where(MachineLoginBackupModel.sandbox_id == sandbox_id)
                .order_by(MachineLoginBackupModel.created_at.desc())
                .limit(1)
            )
        return _entity(row) if row is not None else None

    async def due(self, sandbox_id: UUID, *, reason: str) -> bool:
        latest = await self.latest(sandbox_id)
        if latest is None:
            return True
        interval = DAILY_INTERVAL if reason == "daily" else CLEAN_CLOSE_INTERVAL
        return datetime.now(timezone.utc) - latest.created_at >= interval

    async def back_up(
        self,
        sandbox_id: UUID,
        *,
        acting_user: UUID,
        reason: str,
        force: bool = False,
    ) -> LoginBackup | None:
        """Copy the logins off the machine. None when there is nothing to copy,
        or a copy is not due yet. Expects the browser closed (a clean close
        is what writes the profile back)."""
        if not force and not await self.due(sandbox_id, reason=reason):
            return None
        staged = await run_on_machine(
            sandbox_id,
            acting_user=acting_user,
            command=_tar_command(),
            timeout_seconds=120,
        )
        if staged.exit_code == 3:
            return None
        if not staged.ok:
            logger.warning(
                "workspace.login_backup.stage_failed.degraded",
                sandbox_id=str(sandbox_id),
                exit_code=staged.exit_code,
            )
            return None
        plain = await _read_staged(sandbox_id)
        await run_on_machine(
            sandbox_id,
            acting_user=acting_user,
            command=f"rm -f {_STAGE}",
            timeout_seconds=30,
        )
        return await self._store_backup(sandbox_id, plain, reason=reason)

    async def _store_backup(
        self, sandbox_id: UUID, plain: bytes, *, reason: str
    ) -> LoginBackup:
        key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        sealed = nonce + AESGCM(key).encrypt(nonce, plain, sandbox_id.bytes)
        backup_id = uuid7()
        object_key = f"{sandbox_id}/{backup_id}.bin"
        await _store().put_async(object_key, sealed)
        wrapped = _cipher().encrypt_str(base64.b64encode(key).decode())
        async with self._uow_factory() as uow:
            row = MachineLoginBackupModel(
                id=backup_id,
                sandbox_id=sandbox_id,
                object_key=object_key,
                wrapped_key=wrapped or "",
                size_bytes=len(sealed),
                sha256=hashlib.sha256(plain).hexdigest(),
                reason=reason,
            )
            uow.session.add(row)
            await uow.session.flush()
            backup = _entity(row)
            stale = await self._stale(uow, sandbox_id)
            if stale:
                await uow.session.execute(
                    delete(MachineLoginBackupModel).where(
                        MachineLoginBackupModel.id.in_([r.id for r in stale])
                    )
                )
            await uow.commit()
        for old in stale:
            await _forget_object(old.object_key)
        logger.info(
            "workspace.login_backup.taken.observed",
            sandbox_id=str(sandbox_id),
            reason=reason,
            size_bytes=len(sealed),
        )
        return backup

    async def _stale(self, uow, sandbox_id: UUID) -> list[MachineLoginBackupModel]:
        rows = (
            await uow.session.scalars(
                select(MachineLoginBackupModel)
                .where(MachineLoginBackupModel.sandbox_id == sandbox_id)
                .order_by(MachineLoginBackupModel.created_at.desc())
            )
        ).all()
        return list(rows[KEEP:])

    async def restore(self, sandbox_id: UUID, *, acting_user: UUID) -> bool:
        """Put the newest copy back onto the machine. False when there is none.

        Closes the browser first, so a running Chrome does not write its own
        profile back over the one restored underneath it.
        """
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(MachineLoginBackupModel)
                .where(MachineLoginBackupModel.sandbox_id == sandbox_id)
                .order_by(MachineLoginBackupModel.created_at.desc())
                .limit(1)
            )
        if row is None:
            return False
        plain = await self._open(sandbox_id, row)
        await _write_staged(sandbox_id, plain)
        root = shlex.quote(BROWSER_PROFILE_ROOT)
        outcome = await run_on_machine(
            sandbox_id,
            acting_user=acting_user,
            command=(
                f"{close_browser_command()}; mkdir -p {root} && "
                f"tar xzf {_RESTORE} -C {root} && rm -f {_RESTORE}"
            ),
            timeout_seconds=120,
        )
        logger.info(
            "workspace.login_backup.restored.observed",
            sandbox_id=str(sandbox_id),
            ok=outcome.ok,
        )
        return outcome.ok

    async def restore_if_lost(self, sandbox_id: UUID, *, acting_user: UUID) -> bool:
        """Put the newest copy back on a machine that came up holding no login
        at all -- a disk the provider lost, an expired pause. True when it did.

        Never over a profile that has anything: a login made since the last
        copy is newer than the copy, and replacing it would be the loss this
        is here to prevent.
        """
        if await self.latest(sandbox_id) is None:
            return False
        parts = " ".join(shlex.quote(part) for part in LOGIN_PARTS)
        root = shlex.quote(BROWSER_PROFILE_ROOT)
        held = await run_on_machine(
            sandbox_id,
            acting_user=acting_user,
            command=(
                f"cd {root} 2>/dev/null || exit 1; "
                f'for p in {parts}; do [ -e "$p" ] && exit 0; done; exit 1'
            ),
            timeout_seconds=30,
        )
        if held.ok:
            return False
        return await self.restore(sandbox_id, acting_user=acting_user)

    async def _open(self, sandbox_id: UUID, row: MachineLoginBackupModel) -> bytes:
        result = await _store().get_async(row.object_key)
        sealed = (await result.bytes_async()).to_bytes()
        key = base64.b64decode(_cipher().decrypt_str(row.wrapped_key) or "")
        nonce, body = sealed[:12], sealed[12:]
        return AESGCM(key).decrypt(nonce, body, sandbox_id.bytes)

    async def forget(self, sandbox_id: UUID) -> None:
        """Drop every copy: a person who chose to forget their logins."""
        async with self._uow_factory() as uow:
            rows = (
                await uow.session.scalars(
                    select(MachineLoginBackupModel).where(
                        MachineLoginBackupModel.sandbox_id == sandbox_id
                    )
                )
            ).all()
            await uow.session.execute(
                delete(MachineLoginBackupModel).where(
                    MachineLoginBackupModel.sandbox_id == sandbox_id
                )
            )
            await uow.commit()
        for row in rows:
            await _forget_object(row.object_key)


async def _read_staged(sandbox_id: UUID) -> bytes:
    from app.modules.workspace.services.sandbox_composition import build_local_client

    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    return await build_local_client().read_file(
        sandbox_id, _STAGE, deadline_at=deadline
    )


async def _write_staged(sandbox_id: UUID, data: bytes) -> None:
    from app.modules.workspace.services.sandbox_composition import build_local_client

    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    await build_local_client().write_file(
        sandbox_id, _RESTORE, data, deadline_at=deadline
    )


async def _forget_object(object_key: str) -> None:
    try:
        await _store().delete_async(object_key)
    except FileNotFoundError, OSError:
        logger.warning("workspace.login_backup.object_delete.degraded", exc_info=True)


def _entity(row: MachineLoginBackupModel) -> LoginBackup:
    return LoginBackup(
        id=row.id,
        sandbox_id=row.sandbox_id,
        created_at=row.created_at,
        size_bytes=row.size_bytes,
        reason=row.reason,
    )
