"""Named services on a machine: their durable definitions and their idleness.

A service is a process meant to outlive the run that started it -- a dev
server a preview points at. The process registry in Redis knows the process;
this knows the service, in Postgres, because the service has to outlive the
process too: the machine pauses, the process ends, and the definition is what
starts it again. Kept here:

- what to run and where, and whether to start it again after a pause;
- the caller's own environment variables, encrypted under the deployment's
  secret cipher and revealed only to start the process;
- the claims its Lemma token was minted for, never the token -- a restart
  mints a fresh one for ``started_by`` with exactly the reach the first had;
- how far its output has been read and when it last moved, for the idle
  ceiling: a service keeps its machine busy (and metered) only while it has
  produced output within ``MACHINE_SERVICE_IDLE_SECONDS``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import reveal_secret
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.infrastructure.machine_models import MachineServiceModel


#: The most services one machine keeps. Each is a name a person or an agent
#: chose; past this many something is starting them in a loop, and the next
#: name is refused (``MACHINE_SERVICE_LIMIT``) rather than kept and never
#: brought back.
MAX_SERVICES = 64


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """What a process's Lemma token is minted for: the arguments of
    ``WorkspaceSandboxService.get_env_vars``, kept so a restart mints the same."""

    user_id: UUID
    pod_id: UUID | None = None
    organization_id: UUID | None = None
    workload_type: str | None = None
    workload_id: UUID | None = None
    workload_name: str | None = None
    scope: tuple[str, ...] | None = None
    conversation_id: UUID | None = None
    #: The delegation session, so a restart's token finds the same approvals.
    session_id: str | None = None

    def to_json(self) -> dict[str, object]:
        def text(value: UUID | None) -> str | None:
            return str(value) if value is not None else None

        return {
            "user_id": str(self.user_id),
            "pod_id": text(self.pod_id),
            "organization_id": text(self.organization_id),
            "workload_type": self.workload_type,
            "workload_id": text(self.workload_id),
            "workload_name": self.workload_name,
            "scope": list(self.scope) if self.scope is not None else None,
            "conversation_id": text(self.conversation_id),
            "session_id": self.session_id,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> TokenClaims:
        def uuid_or_none(key: str) -> UUID | None:
            value = data.get(key)
            return UUID(str(value)) if value else None

        scope = data.get("scope")
        return cls(
            user_id=UUID(str(data["user_id"])),
            pod_id=uuid_or_none("pod_id"),
            organization_id=uuid_or_none("organization_id"),
            workload_type=str(data["workload_type"])
            if data.get("workload_type")
            else None,
            workload_id=uuid_or_none("workload_id"),
            workload_name=str(data["workload_name"])
            if data.get("workload_name")
            else None,
            scope=tuple(str(s) for s in scope) if isinstance(scope, list) else None,
            conversation_id=uuid_or_none("conversation_id"),
            session_id=str(data["session_id"]) if data.get("session_id") else None,
        )


async def mint_env(claims: TokenClaims) -> dict[str, str]:
    """A fresh ``LEMMA_TOKEN`` and the rest of a process's Lemma environment,
    for these claims."""
    from app.modules.workspace.services.workspace_sandbox_service import (
        WorkspaceSandboxService,
    )

    service = WorkspaceSandboxService()
    try:
        return await service.get_env_vars(
            claims.user_id,
            claims.pod_id,
            organization_id=claims.organization_id,
            workload_type=claims.workload_type,
            workload_id=claims.workload_id,
            workload_name=claims.workload_name,
            scope=list(claims.scope) if claims.scope is not None else None,
            conversation_id=claims.conversation_id,
            session_id=claims.session_id,
        )
    finally:
        await service.close()


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    machine_id: UUID
    name: str
    command: str
    cwd: str
    claims: TokenClaims | None
    started_by: UUID | None
    restart_on_resume: bool
    process_id: str | None
    env: dict[str, SecretStr] = field(default_factory=dict)

    def revealed_env(self) -> dict[str, str]:
        """The caller's variables in plaintext: only to start the process."""
        return {name: reveal_secret(value) or "" for name, value in self.env.items()}


def _cipher():
    from app.core.crypto.factory import get_secret_cipher

    return get_secret_cipher()


def _seal(env: dict[str, str]) -> str | None:
    return _cipher().encrypt_str(json.dumps(env)) if env else None


def _open(ciphertext: str | None) -> dict[str, SecretStr]:
    if not ciphertext:
        return {}
    plain = json.loads(_cipher().decrypt_str(ciphertext) or "{}")
    return {str(name): SecretStr(str(value)) for name, value in plain.items()}


def _definition(row: MachineServiceModel) -> ServiceDefinition:
    return ServiceDefinition(
        machine_id=row.sandbox_id,
        name=row.name,
        command=row.command,
        cwd=row.cwd,
        claims=TokenClaims.from_json(row.token_claims) if row.token_claims else None,
        started_by=row.started_by,
        restart_on_resume=row.restart_on_resume,
        process_id=row.process_id,
        env=_open(row.env_ciphertext),
    )


class MachineServices:
    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        if uow_factory is None:
            from app.core.api.dependencies import get_uow_factory

            uow_factory = get_uow_factory()
        self._uow_factory = uow_factory

    async def record(
        self,
        machine_id: UUID,
        *,
        name: str,
        command: str,
        cwd: str,
        env: dict[str, str],
        claims: TokenClaims | None,
        started_by: UUID | None,
        restart_on_resume: bool,
        process_id: str,
    ) -> None:
        """The service's definition, replacing any earlier one by its name."""
        now = datetime.now(timezone.utc)
        values = {
            "command": command,
            "cwd": cwd,
            "env_ciphertext": _seal(env),
            "token_claims": claims.to_json() if claims else {},
            "started_by": started_by,
            "restart_on_resume": restart_on_resume,
            "process_id": process_id,
            "output_sequence": 0,
            "last_active_at": now,
            "stopped_at": None,
            "updated_at": now,
        }
        async with self._uow_factory() as uow:
            await uow.session.execute(
                insert(MachineServiceModel)
                .values(sandbox_id=machine_id, name=name, created_at=now, **values)
                .on_conflict_do_update(
                    constraint="uq_machine_services_name", set_=values
                )
            )
            await uow.commit()

    async def running(self, machine_id: UUID) -> list[ServiceDefinition]:
        """Services not stopped by a person: the ones to bring back."""
        async with self._uow_factory() as uow:
            rows = (
                await uow.session.scalars(
                    select(MachineServiceModel)
                    .where(
                        MachineServiceModel.sandbox_id == machine_id,
                        MachineServiceModel.stopped_at.is_(None),
                    )
                    .order_by(MachineServiceModel.name)
                    .limit(MAX_SERVICES)
                )
            ).all()
        return [_definition(row) for row in rows]

    async def others_kept(self, machine_id: UUID, name: str) -> int:
        """How many services besides ``name`` this machine would bring back."""
        async with self._uow_factory() as uow:
            count = await uow.session.scalar(
                select(func.count())
                .select_from(MachineServiceModel)
                .where(
                    MachineServiceModel.sandbox_id == machine_id,
                    MachineServiceModel.name != name,
                    MachineServiceModel.stopped_at.is_(None),
                )
            )
        return int(count or 0)

    async def forget(self, machine_id: UUID, name: str) -> None:
        """One service's definition gone: it is never started again."""
        async with self._uow_factory() as uow:
            await uow.session.execute(
                delete(MachineServiceModel).where(
                    MachineServiceModel.sandbox_id == machine_id,
                    MachineServiceModel.name == name,
                )
            )
            await uow.commit()

    async def forget_all(self, machine_id: UUID) -> None:
        """Every definition gone: the machine was deleted, or its disk replaced.
        A service belongs to the life of the machine it was started on."""
        async with self._uow_factory() as uow:
            await uow.session.execute(
                delete(MachineServiceModel).where(
                    MachineServiceModel.sandbox_id == machine_id
                )
            )
            await uow.commit()

    async def stopped(self, machine_id: UUID, process_id: str) -> None:
        """A person stopped it: it is not started again after a pause."""
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(MachineServiceModel)
                .where(
                    MachineServiceModel.sandbox_id == machine_id,
                    MachineServiceModel.process_id == process_id,
                )
                .values(stopped_at=datetime.now(timezone.utc))
            )
            await uow.commit()

    async def active(
        self,
        machine_id: UUID,
        process_id: str,
        *,
        next_sequence: int,
        now: datetime | None = None,
        idle_after: timedelta,
    ) -> bool:
        """Whether the service on this process still counts as in use: its
        output moved since the last look, or it last moved within
        ``idle_after``. A process that is not a service is always in use."""
        now = now or datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(MachineServiceModel)
                .where(
                    MachineServiceModel.sandbox_id == machine_id,
                    MachineServiceModel.process_id == process_id,
                )
                .with_for_update()
            )
            if row is None:
                return True
            if next_sequence > row.output_sequence:
                row.output_sequence = next_sequence
                row.last_active_at = now
            last = row.last_active_at or row.created_at
            await uow.commit()
        return now - last < idle_after

    async def output_cursor(self, machine_id: UUID, process_id: str) -> int | None:
        """How far this service's output has been read; None when the process
        is not a service."""
        async with self._uow_factory() as uow:
            return await uow.session.scalar(
                select(MachineServiceModel.output_sequence).where(
                    MachineServiceModel.sandbox_id == machine_id,
                    MachineServiceModel.process_id == process_id,
                )
            )


__all__ = [
    "MachineServices",
    "ServiceDefinition",
    "TokenClaims",
    "mint_env",
]
