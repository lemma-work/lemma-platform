"""The rows behind connected machines: pairing, approval, claim, links, revoke.

Every method runs inside the caller's unit of work and commits nothing; the
link's store and the HTTP service each open one per step.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.machine import (
    MachineBillingScope,
    MachineFabric,
    MachineVisibility,
)
from app.modules.workspace.domain.sandbox import (
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.models import SandboxModel
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.domain.machine_connection import (
    connected_machine_id,
    ConnectionLimitReached,
    ConnectionNameTaken,
    ConnectionNotFound,
    MachineConnection,
    PairingAlreadyApproved,
    PairingNotFound,
    PendingPairing,
)
from app.modules.workspace.infrastructure.machine_connection_models import (
    MachineConnectionPairingModel,
)
from app.modules.workspace.infrastructure.machine_models import (
    MachineConnectionModel,
)


#: The most connections one person's listing returns. Plans allow a handful;
#: this only stops a runaway account turning a listing into a table scan.
MAX_LISTED_CONNECTIONS = 200


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def retired_slug(connection_id: UUID) -> str:
    """The slug a revoked connection's machine row is renamed to.

    From the id alone, so it fits ``sandboxes.slug`` whatever the name's length
    (33 of its 64 characters), and with a ``~`` no name may contain
    (``NAME_PATTERN``), so it never takes a name a person could give.
    """
    return f"~{connection_id.hex}"


def _connection(row: MachineConnectionModel) -> MachineConnection:
    return MachineConnection(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        display_name=row.display_name,
        mode=row.mode,
        pod_id=row.pod_id,
        platform=row.platform,
        home=row.home,
        capabilities=dict(row.capabilities or {}),
        runtime_version=row.runtime_version,
        last_seen_at=row.last_seen_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


class MachineConnectionRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

    # ----------------------------------------------------------------- pairing

    async def create_pairing(
        self,
        *,
        code_hash: str,
        claim_key_hash: str,
        display_name: str,
        mode: str,
        platform: str,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> PendingPairing:
        timestamp = now or utcnow()
        await self._expire_pairings(timestamp)
        row = MachineConnectionPairingModel(
            code_hash=code_hash,
            claim_key_hash=claim_key_hash,
            display_name=display_name,
            mode=mode,
            platform=platform,
            expires_at=timestamp + ttl,
        )
        self.session.add(row)
        await self.session.flush()
        return self._pending(row)

    async def _expire_pairings(self, now: datetime) -> None:
        """Expired codes go as new ones arrive; nothing else reads them.

        A code approved but never claimed made a connection and its machine,
        which would otherwise hold the name and a slot of the plan's limit with
        no machine ever able to use them: they are revoked with it. A claim
        deletes its code, so a connection that completed its claim is never
        reached from here.
        """
        unclaimed = (
            await self.session.execute(
                delete(MachineConnectionPairingModel)
                .where(MachineConnectionPairingModel.expires_at <= now)
                .returning(MachineConnectionPairingModel.connection_id)
            )
        ).scalars()
        connection_ids = [value for value in unclaimed if value is not None]
        if not connection_ids:
            return
        revoked = (
            await self.session.execute(
                update(MachineConnectionModel)
                .where(
                    MachineConnectionModel.id.in_(connection_ids),
                    MachineConnectionModel.secret_hash.is_(None),
                    MachineConnectionModel.revoked_at.is_(None),
                )
                .values(revoked_at=now)
                .returning(MachineConnectionModel.id)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        for connection_id in list(revoked):
            await self._retire_machine(connection_id, at=now)

    @staticmethod
    def _pending(row: MachineConnectionPairingModel) -> PendingPairing:
        return PendingPairing(
            pairing_id=row.id,
            display_name=row.display_name,
            mode=row.mode,
            platform=row.platform,
            expires_at=row.expires_at,
            approved=row.connection_id is not None,
        )

    async def _live_pairing(
        self, code_hash: str, *, now: datetime | None, lock: bool = False
    ) -> MachineConnectionPairingModel:
        statement = select(MachineConnectionPairingModel).where(
            MachineConnectionPairingModel.code_hash == code_hash
        )
        if lock:
            statement = statement.with_for_update()
        row = (await self.session.execute(statement)).scalar_one_or_none()
        # The time is read once the row lock is held: a code that expired while
        # this waited on another approval or claim is expired.
        return _unexpired(row, now)

    async def pending_pairing(
        self, code_hash: str, *, now: datetime | None = None
    ) -> PendingPairing:
        return self._pending(await self._live_pairing(code_hash, now=now))

    async def approve_pairing(
        self,
        *,
        code_hash: str,
        user_id: UUID,
        name: str,
        pod_id: UUID | None,
        limit: int | None,
        now: datetime | None = None,
    ) -> MachineConnection:
        """Create the connection the code names, owned by ``user_id``.

        The person's live connections are counted under a transaction-scoped
        advisory lock on the person, so two approvals racing past the plan's
        limit serialise -- a row lock cannot, when there are no rows yet.
        """
        pairing = await self._live_pairing(code_hash, now=now, lock=True)
        if pairing.connection_id is not None:
            raise PairingAlreadyApproved("That code was already approved.")
        if limit is not None:
            await self.session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtext(f"machine-connections:{user_id}")
                    )
                )
            )
            _unexpired(pairing, now)
            live = (
                await self.session.execute(
                    select(func.count())
                    .select_from(MachineConnectionModel)
                    .where(
                        MachineConnectionModel.user_id == user_id,
                        MachineConnectionModel.revoked_at.is_(None),
                    )
                )
            ).scalar_one()
            if live >= limit:
                raise ConnectionLimitReached(
                    f"Your plan allows {limit} connected "
                    f"machine{'s' if limit != 1 else ''}. Disconnect one first."
                )
        row = MachineConnectionModel(
            # One id for the connection and its machine row, tagged so a
            # provider call is routed to the connected fabric by id alone.
            id=connected_machine_id(),
            user_id=user_id,
            pod_id=pod_id,
            name=name,
            display_name=pairing.display_name,
            mode=pairing.mode,
            platform=pairing.platform,
            capabilities={},
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            raise ConnectionNameTaken(
                f"You already have a machine named {name}. Choose another name."
            ) from None
        pairing.connection_id = row.id
        await self._machine_row(row)
        await self.session.flush()
        return _connection(row)

    async def _machine_row(self, row: MachineConnectionModel) -> None:
        """The machine ``mine:<name>`` resolves to: a ``sandboxes`` row of the
        person's, named by the connection and sharing its id.

        ``ensure_row`` hands back a row already holding the slug -- another
        machine of the person's under that name (Desktop's ``host-<hex>``, a
        cloud machine) -- rather than making one; that name is taken.
        """
        machine = await SandboxRepository(self.uow).ensure_row(
            sandbox_id=row.id,
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=row.user_id,
            slug=row.name,
            display_name=row.display_name,
            machine={
                "fabric": MachineFabric.CONNECTED.value,
                "connection_id": row.id,
                "visibility": MachineVisibility.PRIVATE.value,
                "billing_scope": MachineBillingScope.USER.value,
                "created_by": row.user_id,
            },
        )
        if machine.id != row.id:
            raise ConnectionNameTaken(
                f"You already have a machine named {row.name}. Choose another name."
            )

    async def claim_pairing(
        self,
        *,
        code_hash: str,
        claim_key_hash: str,
        secret_hash: str,
        installation_id: str,
        now: datetime | None = None,
    ) -> MachineConnection | None:
        """Give the approved connection its secret and spend the code.

        None while nobody has approved it yet. Raises ``PairingNotFound`` for
        an unknown or expired code, and for a claim key that is not the one the
        code was asked for with -- the same refusal, so a guess learns nothing.
        """
        pairing = await self._live_pairing(code_hash, now=now, lock=True)
        if pairing.claim_key_hash != claim_key_hash:
            raise PairingNotFound("That code is unknown or has expired.")
        if pairing.connection_id is None:
            return None
        row = await self.session.get(MachineConnectionModel, pairing.connection_id)
        if row is None or row.revoked_at is not None:
            raise PairingNotFound("That code is unknown or has expired.")
        row.secret_hash = secret_hash
        row.installation_id = installation_id
        await self.session.delete(pairing)
        await self.session.flush()
        return _connection(row)

    # ------------------------------------------------------------------- links

    async def open_link(
        self,
        *,
        secret_hash: str,
        installation_id: str,
        report: dict[str, object],
        now: datetime | None = None,
    ) -> tuple[MachineConnection, int] | None:
        """Authenticate a ``hello`` and claim the machine's next link generation.

        One ``UPDATE ... RETURNING``, so two handshakes racing on different
        replicas come away with distinct, ordered generations. None for an
        unknown or revoked secret -- deliberately one answer for both.
        """
        timestamp = now or utcnow()
        claimed = (
            await self.session.execute(
                update(MachineConnectionModel)
                .where(
                    MachineConnectionModel.secret_hash == secret_hash,
                    MachineConnectionModel.revoked_at.is_(None),
                )
                .values(
                    link_generation=MachineConnectionModel.link_generation + 1,
                    installation_id=installation_id,
                    last_seen_at=timestamp,
                    **report,
                )
                .returning(MachineConnectionModel.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if claimed is None:
            return None
        row = (
            await self.session.execute(
                select(MachineConnectionModel)
                .where(MachineConnectionModel.id == claimed)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return _connection(row), int(row.link_generation)

    async def heartbeat(
        self,
        connection_id: UUID,
        *,
        generation: int,
        report: dict[str, object],
        now: datetime | None = None,
    ) -> int | None:
        """Record a beat from the link of ``generation``.

        Returns the generation that owns the machine: ``generation`` when the
        beat was recorded, a newer one when that link was superseded -- whose
        report, runtime port included, is the machine's now, so the old link's
        is not written -- and None once the connection is revoked.
        """
        updated = (
            await self.session.execute(
                update(MachineConnectionModel)
                .where(
                    MachineConnectionModel.id == connection_id,
                    MachineConnectionModel.revoked_at.is_(None),
                    MachineConnectionModel.link_generation == generation,
                )
                .values(last_seen_at=now or utcnow(), **report)
                .returning(MachineConnectionModel.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if updated is not None:
            return generation
        return await self.link_generation(connection_id)

    async def link_generation(self, connection_id: UUID) -> int | None:
        """The generation of the link that owns the machine; None once revoked."""
        return (
            await self.session.execute(
                select(MachineConnectionModel.link_generation).where(
                    MachineConnectionModel.id == connection_id,
                    MachineConnectionModel.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    # ------------------------------------------------------------------- reads

    async def get(self, connection_id: UUID) -> MachineConnection | None:
        row = await self.session.get(MachineConnectionModel, connection_id)
        return _connection(row) if row is not None else None

    async def list_for_user(self, user_id: UUID) -> list[MachineConnection]:
        rows = (
            await self.session.execute(
                select(MachineConnectionModel)
                .where(
                    MachineConnectionModel.user_id == user_id,
                    MachineConnectionModel.revoked_at.is_(None),
                )
                .order_by(MachineConnectionModel.created_at)
                .limit(MAX_LISTED_CONNECTIONS)
            )
        ).scalars()
        return [_connection(row) for row in rows]

    async def by_secret(self, secret_hash: str) -> MachineConnection | None:
        row = (
            await self.session.execute(
                select(MachineConnectionModel).where(
                    MachineConnectionModel.secret_hash == secret_hash,
                    MachineConnectionModel.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return _connection(row) if row is not None else None

    async def by_name(self, user_id: UUID, name: str) -> MachineConnection | None:
        row = (
            await self.session.execute(
                select(MachineConnectionModel).where(
                    MachineConnectionModel.user_id == user_id,
                    MachineConnectionModel.name == name,
                    MachineConnectionModel.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return _connection(row) if row is not None else None

    # ------------------------------------------------------------------ revoke

    async def revoke(
        self,
        connection_id: UUID,
        *,
        user_id: UUID | None,
        now: datetime | None = None,
    ) -> MachineConnection:
        """Revoke a live connection. ``user_id`` None is the machine revoking
        itself over its own authenticated link."""
        statement = select(MachineConnectionModel).where(
            MachineConnectionModel.id == connection_id,
            MachineConnectionModel.revoked_at.is_(None),
        )
        if user_id is not None:
            statement = statement.where(MachineConnectionModel.user_id == user_id)
        row = (
            await self.session.execute(statement.with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise ConnectionNotFound("No such connected machine.")
        row.revoked_at = now or utcnow()
        await self._retire_machine(row.id, at=row.revoked_at)
        await self.session.flush()
        return _connection(row)

    async def _retire_machine(self, connection_id: UUID, *, at: datetime) -> None:
        """The machine row goes with its connection, and gives its name back:
        renamed so a machine joined later under the same name gets a row of
        its own."""
        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == connection_id)
            .values(
                desired_state=SandboxDesiredState.DELETED.value,
                slug=retired_slug(connection_id),
                updated_at=at,
            )
        )


def _unexpired(
    row: MachineConnectionPairingModel | None, now: datetime | None
) -> MachineConnectionPairingModel:
    """The code, unless it is unknown or past -- or at -- its ``expires_at``."""
    if row is None or row.expires_at <= (now or utcnow()):
        raise PairingNotFound("That code is unknown or has expired.")
    return row
