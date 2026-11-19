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

A copy has three outcomes, and a caller about to destroy a disk must tell
them apart: **stored** (verified in the object store, and only then are older
copies pruned), **nothing** to copy (a profile that never signed in), or
**failed**. A disk is never replaced on a failed copy: the reset or the move
stops and says so. A partial archive is a failure -- ``tar``'s exit status is
the command's, not ``chmod``'s -- and never displaces a good copy.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid7

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.object_storage import build_object_store, local_object_storage_path
from app.modules.workspace.infrastructure.machine_models import (
    MachineLoginBackupModel,
)
from app.modules.workspace.services.machine_shell import Shell, run_on_machine
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


class BackupOutcome(StrEnum):
    STORED = "stored"
    NOTHING = "nothing"
    NOT_DUE = "not_due"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LoginBackup:
    id: UUID
    sandbox_id: UUID
    created_at: datetime
    size_bytes: int
    reason: str


@dataclass(frozen=True, slots=True)
class BackupResult:
    outcome: BackupOutcome
    backup: LoginBackup | None = None
    #: Why, when it failed.
    detail: str | None = None

    @property
    def failed(self) -> bool:
        return self.outcome is BackupOutcome.FAILED

    @property
    def stored(self) -> bool:
        return self.outcome is BackupOutcome.STORED


class LoginBackupFailed(Exception):
    """The logins could not be copied off a machine whose disk is about to go."""


def _store():
    return build_object_store(
        local_prefix=local_object_storage_path(_PREFIX), remote_prefix=_PREFIX
    )


def _cipher():
    from app.core.crypto.factory import get_secret_cipher

    return get_secret_cipher()


#: How long a clean close may take to let go of the profile.
_CLOSE_WAIT_SECONDS = 10


def close_browser_command(wait_seconds: int = _CLOSE_WAIT_SECONDS) -> str:
    """A clean close: agent-browser copies the profile back only on one.

    Its own exit status is not the answer -- with no browser open it fails,
    and that is a closed browser. What is: the profile's ``SingletonLock``
    gone, or naming a process that is no longer alive (a browser the memory
    guard killed). Exits 4 when a live Chrome still holds the profile after
    ``_CLOSE_WAIT_SECONDS``.
    """
    lock = shlex.quote(f"{BROWSER_PROFILE_ROOT}/profile/SingletonLock")
    return (
        f"{shlex.quote(sandbox_command('agent-browser'))} close --all >/dev/null 2>&1; "
        f"i=0; while [ $i -lt {wait_seconds} ]; do "
        f"t=$(readlink {lock} 2>/dev/null) || exit 0; "
        'kill -0 "${t##*-}" 2>/dev/null || exit 0; '
        "i=$((i+1)); sleep 1; done; exit 4"
    )


def _tar_command() -> str:
    """Exit 3 for nothing to copy; any other failure is tar's own status.
    The archive is checked before it counts: a truncated one fails here."""
    parts = " ".join(shlex.quote(part) for part in LOGIN_PARTS)
    root = shlex.quote(BROWSER_PROFILE_ROOT)
    return (
        f"cd {root} 2>/dev/null || exit 3; "
        f'present=\'\'; for p in {parts}; do [ -e "$p" ] && present="$present\n$p"; done; '
        '[ -n "$present" ] || exit 3; '
        f"rm -f {_STAGE}; "
        f'printf "%b" "$present" | sed "/^$/d" | tar czf {_STAGE} -T - '
        f"&& tar tzf {_STAGE} >/dev/null && chmod 600 {_STAGE}"
    )


#: The login probe's answer for "this profile holds no login": distinct from
#: a shell that failed, so a failure is never read as an empty profile.
_NO_LOGINS = 3


class LoginBackups:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        shell: Shell | None = None,
        read_staged: Callable[[UUID], Awaitable[bytes]] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        # How a command reaches the machine, and how the staged archive is
        # read off it: the machine's runtime unless a caller hands in another.
        self._shell: Shell = shell or run_on_machine
        self._read_staged = read_staged or _read_staged

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
    ) -> BackupResult:
        """Copy the logins off the machine. Expects the browser closed (a
        clean close is what writes the profile back)."""
        if not force and not await self.due(sandbox_id, reason=reason):
            return BackupResult(BackupOutcome.NOT_DUE)
        staged = await self._shell(
            sandbox_id,
            acting_user=acting_user,
            command=_tar_command(),
            timeout_seconds=120,
        )
        if staged.exit_code == 3:
            return BackupResult(BackupOutcome.NOTHING)
        if not staged.ok:
            logger.warning(
                "workspace.login_backup.stage_failed.degraded",
                sandbox_id=str(sandbox_id),
                exit_code=staged.exit_code,
            )
            return BackupResult(
                BackupOutcome.FAILED,
                detail=f"packing the browser profile exited {staged.exit_code}",
            )
        plain = await self._read_staged(sandbox_id)
        await self._shell(
            sandbox_id,
            acting_user=acting_user,
            command=f"rm -f {_STAGE}",
            timeout_seconds=30,
        )
        backup = await self._store_backup(sandbox_id, plain, reason=reason)
        if backup is None:
            return BackupResult(
                BackupOutcome.FAILED,
                detail="the copy did not read back from object storage intact",
            )
        return BackupResult(BackupOutcome.STORED, backup=backup)

    async def require_back_up(
        self, sandbox_id: UUID, *, acting_user: UUID, reason: str
    ) -> BackupResult:
        """A copy before a disk is replaced: the browser closed cleanly and the
        copy stored, or nothing to copy -- otherwise ``LoginBackupFailed``,
        and the caller keeps the disk."""
        closed = await self._shell(
            sandbox_id,
            acting_user=acting_user,
            command=close_browser_command(),
            timeout_seconds=_CLOSE_WAIT_SECONDS + 30,
        )
        if not closed.ok:
            raise LoginBackupFailed(
                "The browser would not close cleanly, so its logins could not be "
                "copied off. The disk was left as it was."
            )
        result = await self.back_up(
            sandbox_id, acting_user=acting_user, reason=reason, force=True
        )
        if result.failed:
            raise LoginBackupFailed(
                f"The browser's logins could not be copied off ({result.detail}). "
                "The disk was left as it was."
            )
        return result

    async def _store_backup(
        self, sandbox_id: UUID, plain: bytes, *, reason: str
    ) -> LoginBackup | None:
        """Stored and verified, then recorded -- and only then are the older
        copies pruned. None when the object did not read back intact."""
        key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        sealed = nonce + AESGCM(key).encrypt(nonce, plain, sandbox_id.bytes)
        backup_id = uuid7()
        object_key = f"{sandbox_id}/{backup_id}.bin"
        await _store().put_async(object_key, sealed)
        if not await _stored_intact(object_key, sealed):
            logger.warning(
                "workspace.login_backup.verify_failed.degraded",
                sandbox_id=str(sandbox_id),
            )
            await _forget_object(object_key)
            return None
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
        outcome = await self._shell(
            sandbox_id,
            acting_user=acting_user,
            command=(
                f"( {close_browser_command()} ) && mkdir -p {root} && "
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
        is here to prevent. So only a probe that ran and found nothing (exit
        ``_NO_LOGINS``) restores; one that failed -- a timeout, a machine not
        answering -- says nothing about the profile, and the next sweep that
        sees the machine resume asks again.
        """
        if await self.latest(sandbox_id) is None:
            return False
        parts = " ".join(shlex.quote(part) for part in LOGIN_PARTS)
        root = shlex.quote(BROWSER_PROFILE_ROOT)
        held = await self._shell(
            sandbox_id,
            acting_user=acting_user,
            command=(
                f"cd {root} 2>/dev/null || exit {_NO_LOGINS}; "
                f'for p in {parts}; do [ -e "$p" ] && exit 0; done; '
                f"exit {_NO_LOGINS}"
            ),
            timeout_seconds=30,
        )
        if held.exit_code != _NO_LOGINS:
            if not held.ok:
                logger.warning(
                    "workspace.login_backup.probe_failed.degraded",
                    sandbox_id=str(sandbox_id),
                    exit_code=held.exit_code,
                )
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


async def _stored_intact(object_key: str, sealed: bytes) -> bool:
    """Read the object back: a put that returned is not a copy that exists."""
    try:
        result = await _store().get_async(object_key)
        stored = (await result.bytes_async()).to_bytes()
    except FileNotFoundError, OSError:
        logger.warning("workspace.login_backup.read_back.degraded", exc_info=True)
        return False
    return hashlib.sha256(stored).digest() == hashlib.sha256(sealed).digest()


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
