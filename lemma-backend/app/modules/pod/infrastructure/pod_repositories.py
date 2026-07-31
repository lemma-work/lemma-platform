from __future__ import annotations

from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.authorization.models import RoleAssignmentModel, RoleModel
from app.composition.pod_identity_wiring import OrganizationMember, User
from app.modules.identity.contracts import normalize_identity_email
from app.modules.pod.domain.ports import (
    PodJoinRequestRepositoryPort,
    PodMemberRepositoryPort,
    PodRepositoryPort,
)
from app.modules.pod.domain.errors import PodConflictError
from app.modules.pod.domain.pod_entities import (
    PodEntity,
    PodJoinRequestEntity,
    PodJoinRequestStatus,
    PodMemberEntity,
)
from app.modules.pod.infrastructure.models import Pod, PodJoinRequest, PodMember
from app.modules.pod.domain.visibility import normalize_role_list


class PodRepository(PodRepositoryPort):
    """Pod repository implementation local to pod module."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(self, entity: PodEntity | PodMemberEntity) -> None:
        if hasattr(entity, "collect_events"):
            events = entity.collect_events()
            if events:
                self.uow.collect_events(events)

    def _raise_pod_name_conflict(self, exc: IntegrityError, name: str) -> None:
        if "uq_pod_active_org_name" in str(exc.orig):
            raise PodConflictError(
                f"Pod with name '{name}' already exists in this organization"
            ) from exc
        raise exc

    def _pod_persistence_data(self, entity: PodEntity, *, exclude_unset: bool) -> dict:
        data = entity.model_dump(exclude_unset=exclude_unset)
        config = data.get("config")
        if hasattr(config, "model_dump"):
            data["config"] = config.model_dump(mode="json")
        return data

    async def create(self, entity: PodEntity) -> PodEntity:
        instance = Pod(**self._pod_persistence_data(entity, exclude_unset=False))
        self.session.add(instance)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._raise_pod_name_conflict(exc, entity.name)
        self._collect_events(entity)
        return instance.to_entity()

    async def get(self, id: UUID) -> Optional[PodEntity]:
        stmt = select(Pod).where(Pod.id == id, Pod.is_deleted.is_(False))
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def get_organization_id(self, pod_id: UUID) -> Optional[UUID]:
        """Lean lookup of a pod's organization id (shared read used by services
        that only need the org scope, not the whole pod)."""
        stmt = select(Pod.organization_id).where(Pod.id == pod_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_config(self, pod_id: UUID) -> dict:
        """Lean lookup of a pod's config blob (empty dict when absent)."""
        stmt = select(Pod.config).where(Pod.id == pod_id)
        return (await self.session.execute(stmt)).scalar_one_or_none() or {}

    async def get_name(self, pod_id: UUID) -> Optional[str]:
        stmt = select(Pod.name).where(Pod.id == pod_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update(self, entity: PodEntity) -> PodEntity:
        stmt = select(Pod).where(Pod.id == entity.id, Pod.is_deleted.is_(False))
        result = await self.session.execute(stmt)
        instance = result.scalars().first()

        if not instance:
            raise ValueError(f"Pod {entity.id} not found")

        data = self._pod_persistence_data(entity, exclude_unset=True)
        for key, value in data.items():
            if key in {"id", "created_at", "updated_at"}:
                continue
            if hasattr(instance, key):
                setattr(instance, key, value)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._raise_pod_name_conflict(exc, entity.name)
        self._collect_events(entity)
        return instance.to_entity()

    async def delete(self, id: UUID) -> bool:
        stmt = (
            update(Pod)
            .where(Pod.id == id, Pod.is_deleted.is_(False))
            .values(is_deleted=True)
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def list_by_org(
        self, organization_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[PodEntity], Optional[str]]:
        query = select(Pod).where(
            Pod.organization_id == organization_id,
            Pod.is_deleted.is_(False),
        )
        if cursor:
            query = query.where(Pod.id > UUID(cursor))
        query = query.order_by(Pod.id).limit(limit + 1)
        result = await self.session.execute(query)
        pods = list(result.scalars().all())

        next_cursor = None
        if len(pods) > limit:
            next_cursor = str(pods[limit - 1].id)
            pods = pods[:limit]

        return [pod.to_entity() for pod in pods], next_cursor

    async def list_by_org_member(
        self,
        organization_id: UUID,
        organization_member_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[PodEntity], Optional[str]]:
        query = (
            select(Pod)
            .join(PodMember, PodMember.pod_id == Pod.id)
            .where(
                Pod.organization_id == organization_id,
                Pod.is_deleted.is_(False),
                PodMember.organization_member_id == organization_member_id,
            )
        )
        if cursor:
            query = query.where(Pod.id > UUID(cursor))
        query = query.order_by(Pod.id).limit(limit + 1)
        result = await self.session.execute(query)
        pods = list(result.scalars().all())

        next_cursor = None
        if len(pods) > limit:
            next_cursor = str(pods[limit - 1].id)
            pods = pods[:limit]

        return [pod.to_entity() for pod in pods], next_cursor


class PodMemberRepository(PodMemberRepositoryPort):
    """PodMember repository implementation local to pod module."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(self, entity: PodEntity | PodMemberEntity) -> None:
        if hasattr(entity, "collect_events"):
            events = entity.collect_events()
            if events:
                self.uow.collect_events(events)

    def _member_persistence_data(self, entity: PodMemberEntity) -> dict:
        return entity.model_dump(
            exclude={"user_id", "user_email", "user_name", "user", "roles"}
        )

    async def _member_roles_by_id(self, member_ids: Sequence[UUID]) -> dict[UUID, list[str]]:
        if not member_ids:
            return {}
        stmt = (
            select(RoleAssignmentModel.principal_id, RoleModel.name)
            .join(RoleModel, RoleModel.id == RoleAssignmentModel.role_id)
            .where(
                RoleAssignmentModel.principal_type == "POD_MEMBER",
                RoleAssignmentModel.principal_id.in_(member_ids),
            )
            .order_by(RoleModel.name)
        )
        rows = (await self.session.execute(stmt)).all()
        roles_by_member_id: dict[UUID, list[str]] = {member_id: [] for member_id in member_ids}
        for member_id, role_name in rows:
            roles_by_member_id.setdefault(member_id, []).append(role_name)
        return {
            member_id: normalize_role_list(role_names)
            for member_id, role_names in roles_by_member_id.items()
        }

    async def _attach_member_roles(self, entities: Sequence[PodMemberEntity]) -> None:
        roles_by_member_id = await self._member_roles_by_id([entity.id for entity in entities])
        for entity in entities:
            entity.roles = roles_by_member_id.get(entity.id, [])

    @staticmethod
    def _member_options():
        return (
            joinedload(PodMember.organization_member).joinedload(OrganizationMember.user),
        )

    async def create(self, entity: PodMemberEntity) -> PodMemberEntity:
        instance = PodMember(**self._member_persistence_data(entity))
        self.session.add(instance)
        await self.session.flush()
        self._collect_events(entity)
        created = await self.get(instance.id)
        return created or instance.to_entity()

    async def get(self, id: UUID) -> Optional[PodMemberEntity]:
        stmt = (
            select(PodMember)
            .options(*self._member_options())
            .where(PodMember.id == id)
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            return None
        entity = instance.to_entity()
        await self._attach_member_roles([entity])
        return entity

    async def get_by_pod_and_id(
        self, pod_id: UUID, pod_member_id: UUID
    ) -> Optional[PodMemberEntity]:
        stmt = (
            select(PodMember)
            .options(*self._member_options())
            .where(
                PodMember.id == pod_member_id,
                PodMember.pod_id == pod_id,
            )
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            return None
        entity = instance.to_entity()
        await self._attach_member_roles([entity])
        return entity

    async def update(self, entity: PodMemberEntity) -> PodMemberEntity:
        stmt = select(PodMember).where(PodMember.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()

        if not instance:
            raise ValueError(f"PodMember {entity.id} not found")

        data = self._member_persistence_data(entity)
        for key, value in data.items():
            if key in {"id", "created_at", "updated_at"}:
                continue
            if hasattr(instance, key):
                setattr(instance, key, value)

        await self.session.flush()
        self._collect_events(entity)
        updated = await self.get(instance.id)
        return updated or instance.to_entity()

    async def delete(self, id: UUID) -> bool:
        stmt = delete(PodMember).where(PodMember.id == id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def delete_entity(self, entity: PodMemberEntity) -> bool:
        stmt = select(PodMember).where(PodMember.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            return False

        self._collect_events(entity)
        await self.session.delete(instance)
        return True

    async def get_by_pod_and_org_member(
        self, pod_id: UUID, org_member_id: UUID
    ) -> Optional[PodMemberEntity]:
        stmt = (
            select(PodMember)
            .options(*self._member_options())
            .where(
                PodMember.pod_id == pod_id,
                PodMember.organization_member_id == org_member_id,
            )
        )
        result = await self.session.execute(stmt)
        member = result.scalars().first()
        if not member:
            return None
        entity = member.to_entity()
        await self._attach_member_roles([entity])
        return entity

    async def get_by_pod_and_user_id(
        self, pod_id: UUID, user_id: UUID
    ) -> Optional[PodMemberEntity]:
        stmt = (
            select(PodMember)
            .join(OrganizationMember, PodMember.organization_member_id == OrganizationMember.id)
            .options(*self._member_options())
            .where(
                PodMember.pod_id == pod_id,
                OrganizationMember.user_id == user_id,
            )
        )
        result = await self.session.execute(stmt)
        member = result.scalars().first()
        if not member:
            return None
        entity = member.to_entity()
        await self._attach_member_roles([entity])
        return entity

    async def get_by_pod_and_user_email(
        self, pod_id: UUID, email: str
    ) -> Optional[PodMemberEntity]:
        normalized = normalize_identity_email(email)
        stmt = (
            select(PodMember)
            .join(OrganizationMember, PodMember.organization_member_id == OrganizationMember.id)
            .join(User, OrganizationMember.user_id == User.id)
            .options(*self._member_options())
            .where(
                PodMember.pod_id == pod_id,
                func.lower(User.email) == normalized,
                User.is_deleted.is_(False),
            )
        )
        result = await self.session.execute(stmt)
        member = result.scalars().first()
        if not member:
            return None
        entity = member.to_entity()
        await self._attach_member_roles([entity])
        return entity

    async def list_pod_members(
        self, pod_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[PodMemberEntity], Optional[str]]:
        query = (
            select(PodMember)
            .options(*self._member_options())
            .where(PodMember.pod_id == pod_id)
        )
        if cursor:
            query = query.where(PodMember.id > UUID(cursor))
        query = query.order_by(PodMember.id).limit(limit + 1)
        result = await self.session.execute(query)
        members = list(result.scalars().all())

        next_cursor = None
        if len(members) > limit:
            next_cursor = str(members[limit - 1].id)
            members = members[:limit]

        entities = [member.to_entity() for member in members]
        await self._attach_member_roles(entities)
        return entities, next_cursor

    async def check_user_has_pod_access(
        self, pod_id: UUID, org_member_id: UUID
    ) -> bool:
        stmt = select(PodMember).where(
            PodMember.pod_id == pod_id,
            PodMember.organization_member_id == org_member_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first() is not None


class PodJoinRequestRepository(PodJoinRequestRepositoryPort):
    """Pod join-request repository implementation local to pod module."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(self, entity: PodJoinRequestEntity) -> None:
        if hasattr(entity, "collect_events"):
            events = entity.collect_events()
            if events:
                self.uow.collect_events(events)

    def _persistence_data(self, entity: PodJoinRequestEntity) -> dict:
        # user_email / user_name are display-only and have no columns.
        return entity.model_dump(exclude={"user_email", "user_name"})

    @staticmethod
    def _entity_with_user(
        instance: PodJoinRequest, user: Optional[User]
    ) -> PodJoinRequestEntity:
        entity = instance.to_entity()
        if user is not None:
            entity.user_email = user.email
            name = " ".join(
                part for part in [user.first_name, user.last_name] if part
            )
            entity.user_name = name or None
        return entity

    async def create(self, entity: PodJoinRequestEntity) -> PodJoinRequestEntity:
        instance = PodJoinRequest(**self._persistence_data(entity))
        self.session.add(instance)
        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def update(self, entity: PodJoinRequestEntity) -> PodJoinRequestEntity:
        stmt = select(PodJoinRequest).where(PodJoinRequest.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            raise ValueError(f"PodJoinRequest {entity.id} not found")

        data = entity.model_dump(exclude_unset=True)
        for key, value in data.items():
            if key in {"id", "created_at", "updated_at"}:
                continue
            if hasattr(instance, key):
                setattr(instance, key, value)

        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def get(self, id: UUID) -> Optional[PodJoinRequestEntity]:
        stmt = (
            select(PodJoinRequest, User)
            .outerjoin(User, PodJoinRequest.user_id == User.id)
            .where(PodJoinRequest.id == id)
        )
        row = (await self.session.execute(stmt)).first()
        if not row:
            return None
        instance, user = row
        return self._entity_with_user(instance, user)

    async def get_pending_by_pod_and_user(
        self, pod_id: UUID, user_id: UUID
    ) -> Optional[PodJoinRequestEntity]:
        stmt = (
            select(PodJoinRequest, User)
            .outerjoin(User, PodJoinRequest.user_id == User.id)
            .where(
                PodJoinRequest.pod_id == pod_id,
                PodJoinRequest.user_id == user_id,
                PodJoinRequest.status == PodJoinRequestStatus.PENDING,
            )
            .order_by(PodJoinRequest.created_at.desc())
        )
        row = (await self.session.execute(stmt)).first()
        if not row:
            return None
        instance, user = row
        return self._entity_with_user(instance, user)

    async def list_by_pod(
        self,
        pod_id: UUID,
        *,
        status: PodJoinRequestStatus | None = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[PodJoinRequestEntity], Optional[str]]:
        query = (
            select(PodJoinRequest, User)
            .outerjoin(User, PodJoinRequest.user_id == User.id)
            .where(PodJoinRequest.pod_id == pod_id)
        )
        if status is not None:
            query = query.where(PodJoinRequest.status == status)
        if cursor:
            query = query.where(PodJoinRequest.id > UUID(cursor))
        query = query.order_by(PodJoinRequest.id).limit(limit + 1)

        rows = list((await self.session.execute(query)).all())

        next_cursor = None
        if len(rows) > limit:
            next_cursor = str(rows[limit - 1][0].id)
            rows = rows[:limit]

        return [
            self._entity_with_user(instance, user) for instance, user in rows
        ], next_cursor
