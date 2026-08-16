"""Durable state transitions for sandboxes and their provider instances.

Every write that advances a sandbox's compute is expressed here so the
ordering constraints live in one place. The important one: a provider object is
recorded *before* it is created, never after. A row describing a container that
may not exist is recoverable -- the sweeper reconciles it. A container with no
row is billable compute nobody owns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.sandbox import (
    DEFAULT_SLUG,
    Sandbox,
    SandboxDesiredState,
    SandboxInstance,
    SandboxInstanceState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.models import (
    SandboxInstanceModel,
    SandboxModel,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SandboxRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    async def get(self, sandbox_id: UUID) -> Sandbox | None:
        row = await self.session.get(SandboxModel, sandbox_id)
        return row.to_entity() if row is not None else None

    async def find_by_slug(
        self,
        *,
        kind: SandboxKind,
        owner_kind: SandboxOwnerKind,
        owner_id: UUID,
        slug: str,
    ) -> Sandbox | None:
        result = await self.session.execute(
            select(SandboxModel).where(
                SandboxModel.kind == kind.value,
                SandboxModel.owner_kind == owner_kind.value,
                SandboxModel.owner_id == owner_id,
                SandboxModel.slug == slug,
            )
        )
        row = result.scalar_one_or_none()
        return row.to_entity() if row is not None else None

    async def list_for_owner(
        self,
        *,
        kind: SandboxKind,
        owner_kind: SandboxOwnerKind,
        owner_id: UUID,
    ) -> tuple[Sandbox, ...]:
        result = await self.session.execute(
            select(SandboxModel)
            .where(
                SandboxModel.kind == kind.value,
                SandboxModel.owner_kind == owner_kind.value,
                SandboxModel.owner_id == owner_id,
                SandboxModel.desired_state != SandboxDesiredState.DELETED.value,
            )
            .order_by(SandboxModel.slug)
        )
        return tuple(row.to_entity() for row in result.scalars())

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    async def ensure_row(
        self,
        *,
        sandbox_id: UUID,
        kind: SandboxKind,
        owner_kind: SandboxOwnerKind,
        owner_id: UUID,
        slug: str = DEFAULT_SLUG,
        display_name: str | None = None,
        profile_name: str = "",
        profile_digest: str = "",
    ) -> Sandbox:
        """Get or create the durable identity. Idempotent by (kind, owner, slug).

        The explicit ``sandbox_id`` is what lets a caller pin the id to the
        owner id, which the pre-consolidation volume labels require in order to
        be adopted rather than orphaned.
        """

        existing = await self.find_by_slug(
            kind=kind, owner_kind=owner_kind, owner_id=owner_id, slug=slug
        )
        if existing is not None:
            return existing

        # Reading then inserting is a race, and a real one: concurrent function
        # invocations for the same pod all find nothing and all insert. Letting
        # the database arbitrate is the only version that is actually safe --
        # the unique index decides a winner and the losers read its row, rather
        # than one caller's request failing on a conflict it did not cause.
        now = utcnow()
        await self.session.execute(
            pg_insert(SandboxModel)
            .values(
                id=sandbox_id,
                kind=kind.value,
                owner_kind=owner_kind.value,
                owner_id=owner_id,
                slug=slug,
                display_name=display_name or slug.replace("-", " ").title(),
                profile_name=profile_name,
                profile_digest=profile_digest,
                desired_state=SandboxDesiredState.PRESENT.value,
                epoch=1,
                storage_generation=1,
                mounts=[],
                last_used_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing()
        )
        await self.session.flush()

        created = await self.find_by_slug(
            kind=kind, owner_kind=owner_kind, owner_id=owner_id, slug=slug
        )
        if created is None:  # pragma: no cover - the insert just succeeded
            raise RuntimeError(f"sandbox row for {owner_id}/{slug} vanished")
        return created

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def begin_instance(
        self,
        *,
        sandbox_id: UUID,
        provider: str,
        provider_id: str,
        provider_volume_id: str | None,
        epoch: int,
    ) -> SandboxInstance:
        """Record a provider object before asking the provider to create it.

        Written first and deliberately: the sweeper can destroy a container
        whose row says CREATING but which never appeared, whereas it cannot
        find a container it was never told about.
        """

        row = SandboxInstanceModel(
            id=uuid7(),
            sandbox_id=sandbox_id,
            epoch=epoch,
            provider=provider,
            provider_id=provider_id,
            provider_volume_id=provider_volume_id,
            state=SandboxInstanceState.CREATING.value,
        )
        self.session.add(row)
        await self.session.flush()
        return row.to_entity()

    async def mark_instance_ready(self, instance_id: UUID) -> None:
        await self.session.execute(
            update(SandboxInstanceModel)
            .where(SandboxInstanceModel.id == instance_id)
            .values(
                state=SandboxInstanceState.READY.value,
                ready_at=utcnow(),
                last_error=None,
            )
        )

    async def mark_instance_error(self, instance_id: UUID, error: str) -> None:
        await self.session.execute(
            update(SandboxInstanceModel)
            .where(SandboxInstanceModel.id == instance_id)
            .values(state=SandboxInstanceState.ERROR.value, last_error=error[:2000])
        )

    async def mark_instance_released(self, instance_id: UUID) -> None:
        await self.session.execute(
            update(SandboxInstanceModel)
            .where(SandboxInstanceModel.id == instance_id)
            .values(
                state=SandboxInstanceState.RELEASED.value, released_at=utcnow()
            )
        )

    async def mark_instance_destroyed(self, instance_id: UUID) -> None:
        await self.session.execute(
            update(SandboxInstanceModel)
            .where(SandboxInstanceModel.id == instance_id)
            .values(state=SandboxInstanceState.DESTROYED.value)
        )

    async def current_instance(self, sandbox_id: UUID) -> SandboxInstance | None:
        """The instance at the sandbox's current epoch, whatever its state."""

        sandbox = await self.session.get(SandboxModel, sandbox_id)
        if sandbox is None:
            return None
        result = await self.session.execute(
            select(SandboxInstanceModel).where(
                SandboxInstanceModel.sandbox_id == sandbox_id,
                SandboxInstanceModel.epoch == sandbox.epoch,
            )
        )
        row = result.scalar_one_or_none()
        return row.to_entity() if row is not None else None

    async def list_reclaimable_instances(
        self, *, provider: str
    ) -> tuple[SandboxInstance, ...]:
        """Instances the provider may still be holding compute for."""

        result = await self.session.execute(
            select(SandboxInstanceModel).where(
                SandboxInstanceModel.provider == provider,
                SandboxInstanceModel.state.in_(
                    (
                        SandboxInstanceState.CREATING.value,
                        SandboxInstanceState.READY.value,
                        SandboxInstanceState.ERROR.value,
                    )
                ),
            )
        )
        return tuple(row.to_entity() for row in result.scalars())

    async def bump_epoch(self, sandbox_id: UUID) -> int:
        """Advance the fence so a new container gets a name nothing else holds."""

        result = await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == sandbox_id)
            .values(epoch=SandboxModel.epoch + 1, updated_at=utcnow())
            .returning(SandboxModel.epoch)
        )
        return int(result.scalar_one())

    async def bump_storage_generation(self, sandbox_id: UUID) -> int:
        """Record that the durable disk was replaced, so agents are told."""

        result = await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == sandbox_id)
            .values(
                storage_generation=SandboxModel.storage_generation + 1,
                updated_at=utcnow(),
            )
            .returning(SandboxModel.storage_generation)
        )
        return int(result.scalar_one())

    async def set_provider_volume(self, sandbox_id: UUID, volume_id: str) -> None:
        """Pin the volume this sandbox owns, once it is adopted or created."""

        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == sandbox_id)
            .values(provider_volume_id=volume_id, updated_at=utcnow())
        )

    async def set_profile(
        self, sandbox_id: UUID, *, name: str, digest: str
    ) -> None:
        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == sandbox_id)
            .values(profile_name=name, profile_digest=digest, updated_at=utcnow())
        )

    async def set_desired_state(
        self, sandbox_id: UUID, state: SandboxDesiredState
    ) -> None:
        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == sandbox_id)
            .values(desired_state=state.value, updated_at=utcnow())
        )

    async def touch(self, sandbox_id: UUID) -> None:
        """Activity, which is what idle reclamation is measured against."""

        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == sandbox_id)
            .values(last_used_at=utcnow())
        )

    async def mark_in_use(self, sandbox_id: UUID, instance_id: UUID | None) -> None:
        """Record that this sandbox is serving again, not merely that it was used.

        Adopting a sandbox is a state change, and the resume path treated it as
        a read: only a full provision wrote `PRESENT` back, so after the first
        release the row kept saying RELEASED however many times the sandbox was
        resumed and used. `list_idle` selects on `desired_state == PRESENT`, so
        the sandbox went invisible to idle release for the rest of its life --
        nothing stopped its compute, it ran until the provider's own timeout,
        and on E2B the action at that timeout was to delete it.

        Folded into the same statement as the activity stamp because that write
        already happens on this path, so correcting the state costs nothing
        extra, and because doing it as one conditional UPDATE rather than
        read-then-write leaves no window for a concurrent release to be undone
        by a stale value.
        """
        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == sandbox_id)
            .values(
                last_used_at=utcnow(),
                desired_state=SandboxDesiredState.PRESENT.value,
                updated_at=utcnow(),
            )
        )
        if instance_id is None:
            return
        # Never resurrect an instance that is finished with. A destroyed row
        # describes a provider object that is gone, and marking it ready would
        # point the next ensure at nothing.
        await self.session.execute(
            update(SandboxInstanceModel)
            .where(
                SandboxInstanceModel.id == instance_id,
                SandboxInstanceModel.state != SandboxInstanceState.DESTROYED.value,
            )
            .values(state=SandboxInstanceState.READY.value, last_error=None)
        )

    async def list_idle(
        self, *, idle_before: datetime, limit: int = 100
    ) -> tuple[Sandbox, ...]:
        result = await self.session.execute(
            select(SandboxModel)
            .where(
                SandboxModel.desired_state == SandboxDesiredState.PRESENT.value,
                SandboxModel.last_used_at < idle_before,
            )
            .order_by(SandboxModel.last_used_at)
            .limit(limit)
        )
        return tuple(row.to_entity() for row in result.scalars())
