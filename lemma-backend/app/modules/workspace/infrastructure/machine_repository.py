"""Machines as rows: finding, listing, creating and changing them.

A machine is a ``sandboxes`` row of kind ``workspace``; this reads those rows
with their current instance and turns them into ``Machine``. Status comes from
the rows, not the provider: a listing must not cost a provider round trip per
machine, and the rows already say whether a machine was last left running.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.orm import aliased

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.machine import (
    Capabilities,
    Machine,
    MachineBillingScope,
    MachineFabric,
    MachineKind,
    MachineSize,
    MachineStatus,
    MachineTemplate,
    MachineVisibility,
    LEGACY_DEFAULT_SIZE,
)
from app.modules.workspace.domain.sandbox import (
    DEFAULT_SLUG,
    SandboxDesiredState,
    SandboxInstanceState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.models import (
    SandboxInstanceModel,
    SandboxModel,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository

_DEFAULT_DISPLAY_NAME = "My machine"


class MachineRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    async def get(self, machine_id: UUID) -> Machine | None:
        found = await self._select(SandboxModel.id == machine_id)
        return found[0] if found else None

    async def default_for(self, user_id: UUID) -> Machine | None:
        found = await self._select(
            SandboxModel.owner_kind == SandboxOwnerKind.USER.value,
            SandboxModel.owner_id == user_id,
            SandboxModel.slug == DEFAULT_SLUG,
        )
        return found[0] if found else None

    async def pod_machine(self, pod_id: UUID, name: str) -> Machine | None:
        found = await self._select(
            SandboxModel.owner_kind == SandboxOwnerKind.POD.value,
            SandboxModel.owner_id == pod_id,
            SandboxModel.slug == name,
        )
        return found[0] if found else None

    async def connected(self, user_id: UUID, name: str) -> Machine | None:
        found = await self._select(
            SandboxModel.owner_kind == SandboxOwnerKind.USER.value,
            SandboxModel.owner_id == user_id,
            SandboxModel.slug == name,
            SandboxModel.connection_id.is_not(None),
        )
        return found[0] if found else None

    async def list_for_pod(self, pod_id: UUID) -> tuple[Machine, ...]:
        return await self._select(
            SandboxModel.owner_kind == SandboxOwnerKind.POD.value,
            SandboxModel.owner_id == pod_id,
        )

    async def list_for_user(self, user_id: UUID) -> tuple[Machine, ...]:
        """A person's own machines: their default and what they connected."""
        return await self._select(
            SandboxModel.owner_kind == SandboxOwnerKind.USER.value,
            SandboxModel.owner_id == user_id,
        )

    async def count_pod_machines(
        self, pod_ids: Iterable[UUID] | Select[tuple[UUID]]
    ) -> int:
        """Live machines across these pods -- a list of ids, or a subquery of
        them; what an org's machine cap counts. Counted, not loaded."""
        count = await self.session.scalar(
            select(func.count())
            .select_from(SandboxModel)
            .where(
                SandboxModel.kind == SandboxKind.WORKSPACE.value,
                SandboxModel.desired_state != SandboxDesiredState.DELETED.value,
                SandboxModel.owner_kind == SandboxOwnerKind.POD.value,
                SandboxModel.owner_id.in_(pod_ids),
            )
        )
        return int(count or 0)

    async def count_running(
        self, *, owner_kind: SandboxOwnerKind, owner_ids: Iterable[UUID]
    ) -> int:
        wanted = list(owner_ids)
        if not wanted:
            return 0
        machines = await self._select(
            SandboxModel.owner_kind == owner_kind.value,
            SandboxModel.owner_id.in_(wanted),
        )
        return sum(1 for m in machines if m.status is MachineStatus.RUNNING)

    async def _select(self, *where) -> tuple[Machine, ...]:
        instance = aliased(SandboxInstanceModel)
        result = await self.session.execute(
            select(SandboxModel, instance)
            .outerjoin(
                instance,
                and_(
                    instance.sandbox_id == SandboxModel.id,
                    instance.epoch == SandboxModel.epoch,
                ),
            )
            .where(
                SandboxModel.kind == SandboxKind.WORKSPACE.value,
                SandboxModel.desired_state != SandboxDesiredState.DELETED.value,
                *where,
            )
            .order_by(SandboxModel.owner_kind.desc(), SandboxModel.slug)
        )
        return tuple(to_machine(row, current) for row, current in result.all())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def ensure_default(self, user_id: UUID, *, size: MachineSize) -> Machine:
        """The person's default machine, created at ``size`` if it is new.

        The id is the user id -- every existing workspace caller addresses the
        default machine by it.
        """
        await SandboxRepository(self.uow).ensure_row(
            sandbox_id=user_id,
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user_id,
            slug=DEFAULT_SLUG,
            display_name=_DEFAULT_DISPLAY_NAME,
            machine={
                "size": size.label,
                "template": MachineTemplate.WORKSPACE.value,
                "visibility": MachineVisibility.PRIVATE.value,
                "billing_scope": MachineBillingScope.USER.value,
                "created_by": user_id,
            },
        )
        machine = await self.default_for(user_id)
        if machine is None:  # pragma: no cover - just written
            raise RuntimeError(f"default machine for {user_id} vanished")
        return machine

    async def create_pod_machine(
        self,
        *,
        pod_id: UUID,
        name: str,
        display_name: str,
        template: MachineTemplate,
        size: MachineSize,
        visibility: MachineVisibility,
        run_slots: int,
        created_by: UUID,
    ) -> Machine | None:
        """None when the pod already has a machine of that name."""
        if await self.pod_machine(pod_id, name) is not None:
            return None
        await self._revive_or_insert(
            pod_id=pod_id,
            name=name,
            values={
                "display_name": display_name,
                "template": template.value,
                "size": size.label,
                "visibility": visibility.value,
                "billing_scope": MachineBillingScope.ORG.value,
                "run_slots": run_slots,
                "created_by": created_by,
                "capabilities": Capabilities.for_template(template).to_json(),
            },
        )
        return await self.pod_machine(pod_id, name)

    async def _revive_or_insert(
        self, *, pod_id: UUID, name: str, values: dict[str, object]
    ) -> None:
        """A deleted machine's row is kept (the sweep reclaims its provider
        objects by it), so recreating the name takes that row back with a new
        storage generation rather than colliding with it on the unique key."""
        repository = SandboxRepository(self.uow)
        existing = await repository.find_by_slug(
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.POD,
            owner_id=pod_id,
            slug=name,
        )
        if existing is None:
            await repository.ensure_row(
                sandbox_id=uuid7(),
                kind=SandboxKind.WORKSPACE,
                owner_kind=SandboxOwnerKind.POD,
                owner_id=pod_id,
                slug=name,
                display_name=str(values["display_name"]),
                machine={k: v for k, v in values.items() if k != "display_name"},
            )
            return
        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == existing.id)
            .values(
                **values,
                desired_state=SandboxDesiredState.PRESENT.value,
                storage_generation=SandboxModel.storage_generation + 1,
                provider_volume_id=None,
                last_used_at=_now(),
                updated_at=_now(),
            )
        )

    async def update_machine(self, machine_id: UUID, **values: object) -> None:
        if not values:
            return
        await self.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == machine_id)
            .values(**values, updated_at=_now())
        )

    async def default_machines_at(
        self, size: MachineSize, *, limit: int
    ) -> tuple[Machine, ...]:
        """Default machines still built at ``size``, for the size migration."""
        machines = await self._select(
            SandboxModel.owner_kind == SandboxOwnerKind.USER.value,
            SandboxModel.slug == DEFAULT_SLUG,
            SandboxModel.size == size.label,
        )
        return machines[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def to_machine(row: SandboxModel, instance: SandboxInstanceModel | None) -> Machine:
    owner_kind = SandboxOwnerKind(row.owner_kind)
    if owner_kind is SandboxOwnerKind.POD:
        kind = MachineKind.POD
    elif row.connection_id is not None:
        kind = MachineKind.CONNECTED
    else:
        kind = MachineKind.DEFAULT
    template = _template(row.template)
    capabilities = (
        Capabilities.from_json(row.capabilities)
        if row.capabilities
        else Capabilities.for_template(template)
    )
    return Machine(
        id=row.id,
        kind=kind,
        name="me" if kind is MachineKind.DEFAULT else row.slug,
        display_name=row.display_name,
        template=template,
        size=_size(row.size),
        fabric=_fabric(row.fabric, kind),
        capabilities=capabilities,
        visibility=MachineVisibility(row.visibility),
        billing_scope=MachineBillingScope(row.billing_scope),
        owner_id=row.owner_id,
        pod_id=row.owner_id if kind is MachineKind.POD else None,
        created_by=row.created_by,
        connection_id=row.connection_id,
        run_slots=row.run_slots,
        status=_status(row, instance),
        last_used_at=row.last_used_at,
    )


def _template(value: str) -> MachineTemplate:
    try:
        return MachineTemplate(value)
    except ValueError:
        return MachineTemplate.WORKSPACE


def _size(value: str) -> MachineSize:
    try:
        return MachineSize.parse(value)
    except ValueError:
        return LEGACY_DEFAULT_SIZE


def _fabric(value: str | None, kind: MachineKind) -> MachineFabric:
    if kind is MachineKind.CONNECTED:
        return MachineFabric.CONNECTED
    if value is None:
        from app.modules.workspace.config import workspace_settings

        value = workspace_settings.provider
    try:
        return MachineFabric(value)
    except ValueError:
        return MachineFabric.DOCKER


def _status(row: SandboxModel, instance: SandboxInstanceModel | None) -> MachineStatus:
    desired = SandboxDesiredState(row.desired_state)
    if desired is SandboxDesiredState.DELETED:
        return MachineStatus.DELETED
    if instance is None or desired is SandboxDesiredState.RELEASED:
        return MachineStatus.STOPPED
    state = SandboxInstanceState(instance.state)
    if state is SandboxInstanceState.READY:
        return MachineStatus.RUNNING
    if state is SandboxInstanceState.CREATING:
        return MachineStatus.STARTING
    if state is SandboxInstanceState.ERROR:
        return MachineStatus.ERROR
    return MachineStatus.STOPPED
