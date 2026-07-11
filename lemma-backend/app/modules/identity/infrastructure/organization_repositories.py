from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.errors import (
    OrganizationConflictError,
    OrganizationInvitationNotFoundError,
    OrganizationMemberNotFoundError,
    OrganizationNotFoundError,
)
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationInvitationEntity,
    OrganizationInvitationStatus,
    OrganizationJoinPolicy,
    OrganizationMemberEntity,
)
from app.modules.identity.domain.ports import OrganizationRepositoryPort
from app.modules.identity.infrastructure.models import (
    Organization,
    OrganizationInvitation,
    OrganizationMember,
    User,
)


class OrganizationRepository(OrganizationRepositoryPort):
    """Organization repository implementation local to identity module."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(
        self,
        entity: OrganizationEntity
        | OrganizationMemberEntity
        | OrganizationInvitationEntity,
    ) -> None:
        if hasattr(entity, "collect_events"):
            events = entity.collect_events()
            if events:
                self.uow.collect_events(events)

    def _apply_invitation_status_filter(
        self,
        query,
        *,
        status: OrganizationInvitationStatus | None,
    ):
        if status is None:
            return query

        now = datetime.now(timezone.utc)
        if status == OrganizationInvitationStatus.PENDING:
            return query.where(
                OrganizationInvitation.status == OrganizationInvitationStatus.PENDING,
                OrganizationInvitation.expires_at > now,
            )
        if status == OrganizationInvitationStatus.EXPIRED:
            return query.where(
                or_(
                    OrganizationInvitation.status == OrganizationInvitationStatus.EXPIRED,
                    and_(
                        OrganizationInvitation.status
                        == OrganizationInvitationStatus.PENDING,
                        OrganizationInvitation.expires_at <= now,
                    ),
                )
            )

        return query.where(OrganizationInvitation.status == status)

    async def create(self, entity: OrganizationEntity) -> OrganizationEntity:
        instance = Organization(**entity.model_dump())
        self.session.add(instance)
        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def get(self, id: UUID) -> Optional[OrganizationEntity]:
        stmt = select(Organization).where(Organization.id == id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def get_by_name(self, name: str) -> Optional[OrganizationEntity]:
        stmt = select(Organization).where(Organization.name == name)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def get_by_slug(self, slug: str) -> Optional[OrganizationEntity]:
        stmt = select(Organization).where(Organization.slug == slug)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def update(self, entity: OrganizationEntity) -> OrganizationEntity:
        stmt = select(Organization).where(Organization.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            raise OrganizationNotFoundError()

        instance.name = entity.name
        instance.slug = entity.slug
        instance.email_domain = entity.email_domain
        instance.join_policy = entity.join_policy.value
        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def get_email_domain_org(
        self, email_domain: str
    ) -> Optional[OrganizationEntity]:
        """Return the org that has claimed this domain for EMAIL_DOMAIN self-join."""
        stmt = select(Organization).where(
            func.lower(Organization.email_domain) == email_domain.lower(),
            Organization.join_policy == OrganizationJoinPolicy.EMAIL_DOMAIN.value,
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def list_auto_join_organizations_by_email_domain(
        self,
        email_domain: str,
        user_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationEntity], Optional[str]]:
        query = select(Organization).where(
            func.lower(Organization.email_domain) == email_domain.lower(),
            Organization.join_policy == OrganizationJoinPolicy.EMAIL_DOMAIN.value,
            ~select(OrganizationMember.id)
            .where(
                OrganizationMember.organization_id == Organization.id,
                OrganizationMember.user_id == user_id,
            )
            .exists(),
        )
        if cursor:
            query = query.where(Organization.id > UUID(cursor))
        query = query.order_by(Organization.id).limit(limit + 1)

        result = await self.session.execute(query)
        orgs = list(result.scalars().all())

        next_cursor = None
        if len(orgs) > limit:
            next_cursor = str(orgs[limit - 1].id)
            orgs = orgs[:limit]

        return [o.to_entity() for o in orgs], next_cursor

    async def add_member(
        self, entity: OrganizationMemberEntity
    ) -> OrganizationMemberEntity:
        member = OrganizationMember(
            id=entity.id,
            user_id=entity.user_id,
            organization_id=entity.organization_id,
            role=entity.role,
        )
        self.session.add(member)
        await self.session.flush()
        await self.session.refresh(member, attribute_names=["user"])
        self._collect_events(entity)
        return member.to_entity()

    async def get_member(
        self, user_id: UUID, organization_id: UUID
    ) -> Optional[OrganizationMemberEntity]:
        stmt = (
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == organization_id,
            )
        )
        result = await self.session.execute(stmt)
        member = result.scalars().first()
        return member.to_entity() if member else None

    async def get_member_by_id(
        self, member_id: UUID
    ) -> Optional[OrganizationMemberEntity]:
        stmt = (
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(OrganizationMember.id == member_id)
        )
        result = await self.session.execute(stmt)
        member = result.scalars().first()
        return member.to_entity() if member else None

    async def get_member_by_email(
        self, organization_id: UUID, email: str
    ) -> Optional[OrganizationMemberEntity]:
        normalized = normalize_identity_email(email)
        stmt = (
            select(OrganizationMember)
            .join(User, OrganizationMember.user_id == User.id)
            .options(joinedload(OrganizationMember.user))
            .where(
                OrganizationMember.organization_id == organization_id,
                func.lower(User.email) == normalized,
            )
        )
        result = await self.session.execute(stmt)
        member = result.scalars().first()
        return member.to_entity() if member else None

    async def list_organization_members(
        self, organization_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[OrganizationMemberEntity], Optional[str]]:
        query = (
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(OrganizationMember.organization_id == organization_id)
        )
        if cursor:
            query = query.where(OrganizationMember.id > UUID(cursor))
        query = query.order_by(OrganizationMember.id).limit(limit + 1)

        result = await self.session.execute(query)
        members = list(result.scalars().all())

        next_cursor = None
        if len(members) > limit:
            next_cursor = str(members[limit - 1].id)
            members = members[:limit]

        return [m.to_entity() for m in members], next_cursor

    async def get_user_organizations(
        self, user_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[OrganizationEntity], Optional[str]]:
        query = (
            select(Organization)
            .join(OrganizationMember)
            .where(OrganizationMember.user_id == user_id)
        )
        if cursor:
            query = query.where(Organization.id > UUID(cursor))
        query = query.order_by(Organization.id).limit(limit + 1)

        result = await self.session.execute(query)
        orgs = list(result.scalars().all())

        next_cursor = None
        if len(orgs) > limit:
            next_cursor = str(orgs[limit - 1].id)
            orgs = orgs[:limit]

        return [o.to_entity() for o in orgs], next_cursor

    async def update_member(
        self, entity: OrganizationMemberEntity
    ) -> OrganizationMemberEntity:
        stmt = (
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(OrganizationMember.id == entity.id)
        )
        result = await self.session.execute(stmt)
        member = result.scalars().first()

        if not member:
            raise OrganizationMemberNotFoundError()

        member.role = entity.role
        await self.session.flush()
        self._collect_events(entity)
        return member.to_entity()

    async def delete_member(self, member_id: UUID) -> bool:
        stmt = select(OrganizationMember).where(OrganizationMember.id == member_id)
        result = await self.session.execute(stmt)
        member = result.scalars().first()
        if not member:
            return False
        await self.session.delete(member)
        return True

    async def add_invitation(
        self, entity: OrganizationInvitationEntity
    ) -> OrganizationInvitationEntity:
        invitation = OrganizationInvitation(
            **entity.model_dump(
                exclude={"organization_name", "pod_name", "pod_description"}
            )
        )
        self.session.add(invitation)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            if "uq_org_invitation_pending_email_org_lower" in str(exc.orig):
                raise OrganizationConflictError(
                    "An invitation already exists for this email"
                ) from exc
            raise
        self._collect_events(entity)
        return invitation.to_entity()

    async def get_invitation_by_id(
        self, invitation_id: UUID
    ) -> Optional[OrganizationInvitationEntity]:
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id
        )
        result = await self.session.execute(stmt)
        invitation = result.scalars().first()
        return invitation.to_entity() if invitation else None

    async def get_invitation_by_email(
        self, organization_id: UUID, email: str
    ) -> Optional[OrganizationInvitationEntity]:
        normalized = normalize_identity_email(email)
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == organization_id,
            func.lower(OrganizationInvitation.email) == normalized,
            OrganizationInvitation.status == OrganizationInvitationStatus.PENDING,
        )
        result = await self.session.execute(stmt)
        invitation = result.scalars().first()
        return invitation.to_entity() if invitation else None

    async def list_organization_invitations(
        self,
        organization_id: UUID,
        status: OrganizationInvitationStatus | None = OrganizationInvitationStatus.PENDING,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationInvitationEntity], Optional[str]]:
        query = select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == organization_id
        )
        query = self._apply_invitation_status_filter(query, status=status)

        if cursor:
            query = query.where(OrganizationInvitation.id > UUID(cursor))

        query = query.order_by(OrganizationInvitation.id).limit(limit + 1)

        result = await self.session.execute(query)
        invitations = list(result.scalars().all())

        next_cursor = None
        if len(invitations) > limit:
            next_cursor = str(invitations[limit - 1].id)
            invitations = invitations[:limit]

        return [i.to_entity() for i in invitations], next_cursor

    async def list_user_invitations(
        self,
        user_email: str,
        status: OrganizationInvitationStatus | None = OrganizationInvitationStatus.PENDING,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[OrganizationInvitationEntity], Optional[str]]:
        normalized = normalize_identity_email(user_email)
        query = select(OrganizationInvitation).where(
            func.lower(OrganizationInvitation.email) == normalized
        )
        query = self._apply_invitation_status_filter(query, status=status)

        if cursor:
            query = query.where(OrganizationInvitation.id > UUID(cursor))

        query = query.order_by(OrganizationInvitation.id).limit(limit + 1)

        result = await self.session.execute(query)
        invitations = list(result.scalars().all())

        next_cursor = None
        if len(invitations) > limit:
            next_cursor = str(invitations[limit - 1].id)
            invitations = invitations[:limit]

        return [i.to_entity() for i in invitations], next_cursor

    async def update_invitation(
        self, entity: OrganizationInvitationEntity
    ) -> OrganizationInvitationEntity:
        stmt = select(OrganizationInvitation).where(OrganizationInvitation.id == entity.id)
        result = await self.session.execute(stmt)
        invitation = result.scalars().first()
        if not invitation:
            raise OrganizationInvitationNotFoundError()

        invitation.email = entity.email
        invitation.organization_id = entity.organization_id
        invitation.role = entity.role
        invitation.pod_id = entity.pod_id
        invitation.pod_role = entity.pod_role
        invitation.redirect_uri = entity.redirect_uri
        invitation.status = entity.status
        invitation.expires_at = entity.expires_at
        invitation.accepted_at = entity.accepted_at
        invitation.revoked_at = entity.revoked_at

        await self.session.flush()
        self._collect_events(entity)
        return invitation.to_entity()

    async def delete_invitation(self, invitation_id: UUID) -> bool:
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id
        )
        result = await self.session.execute(stmt)
        invitation = result.scalars().first()
        if invitation:
            await self.session.delete(invitation)
            return True
        return False

    async def delete_invitation_entity(
        self,
        entity: OrganizationInvitationEntity,
    ) -> bool:
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.id == entity.id
        )
        result = await self.session.execute(stmt)
        invitation = result.scalars().first()
        if not invitation:
            raise OrganizationInvitationNotFoundError()

        self._collect_events(entity)
        await self.session.delete(invitation)
        return True
