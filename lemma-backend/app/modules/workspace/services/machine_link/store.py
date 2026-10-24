"""The database half of a compute link: one short transaction per step.

The same shape as ``AgentHostLinkStore``: a link lives for hours, so nothing
here holds a unit of work across an await on the socket. Each method opens
one, does its repository work, commits, and returns plain values.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts.compute_link import MachineReport
from app.modules.workspace.domain.machine_connection import (
    MachineConnection,
    generate_machine_secret,
    normalize_pairing_code,
    secret_hash,
)
from app.modules.workspace.infrastructure.machine_connection_repository import (
    MachineConnectionRepository,
)


@dataclass(frozen=True, slots=True)
class ClaimedPairing:
    connection: MachineConnection
    #: The machine's secret, in plaintext exactly once: in this value, on its
    #: way into the ``paired`` frame.
    machine_secret: str


def report_columns(report: MachineReport) -> dict[str, object]:
    """The connection row's columns a ``hello`` or ``control`` report sets."""
    return {
        "platform": report.platform,
        "home": report.home,
        "runtime_version": report.runtime_version,
        "capabilities": {
            **report.capabilities.model_dump(mode="json"),
            "runtime_ready": report.runtime_ready,
            "runtime_port": report.runtime_port,
            "relay_port": report.relay_port,
        },
    }


class MachineLinkStore:
    def __init__(
        self, uow_factory: Callable[[], SqlAlchemyUnitOfWork] | None = None
    ) -> None:
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)

    async def claim_pairing(
        self, *, code: str, claim_key: str, installation_id: str
    ) -> ClaimedPairing | None:
        """The approved connection and its new secret; None until approved.

        Raises ``PairingNotFound`` for a code that cannot be claimed.
        """
        secret = generate_machine_secret()
        async with self._uow_factory() as uow:
            connection = await MachineConnectionRepository(uow).claim_pairing(
                code_hash=secret_hash(normalize_pairing_code(code)),
                claim_key_hash=secret_hash(claim_key),
                secret_hash=secret_hash(secret),
                installation_id=installation_id,
            )
            if connection is None:
                return None
            await uow.commit()
        return ClaimedPairing(connection=connection, machine_secret=secret)

    async def open_link(
        self, *, secret: str, installation_id: str, report: MachineReport
    ) -> tuple[MachineConnection, int] | None:
        async with self._uow_factory() as uow:
            opened = await MachineConnectionRepository(uow).open_link(
                secret_hash=secret_hash(secret),
                installation_id=installation_id,
                report=report_columns(report),
            )
            await uow.commit()
        return opened

    async def heartbeat(self, connection_id: UUID, report: MachineReport) -> bool:
        async with self._uow_factory() as uow:
            alive = await MachineConnectionRepository(uow).heartbeat(
                connection_id, report=report_columns(report)
            )
            await uow.commit()
        return alive

    async def link_generation(self, connection_id: UUID) -> int | None:
        async with self._uow_factory() as uow:
            return await MachineConnectionRepository(uow).link_generation(connection_id)

    async def revoke_self(self, connection_id: UUID) -> MachineConnection:
        async with self._uow_factory() as uow:
            revoked = await MachineConnectionRepository(uow).revoke(
                connection_id, user_id=None
            )
            await uow.commit()
        return revoked

    async def connection(self, connection_id: UUID) -> MachineConnection | None:
        async with self._uow_factory() as uow:
            return await MachineConnectionRepository(uow).get(connection_id)

    async def machine_by_secret(self, secret: str) -> MachineConnection | None:
        """The live connection a secret names, for the machine's HTTP routes."""
        async with self._uow_factory() as uow:
            return await MachineConnectionRepository(uow).by_secret(secret_hash(secret))
