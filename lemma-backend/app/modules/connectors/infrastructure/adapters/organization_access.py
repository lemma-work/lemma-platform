"""Connectors' side of :class:`OrganizationAccessPort`, over identity's operations.

This was `app/composition/connector_identity.py`, and what it actually held was
two `select()` statements against `organizations` and `organization_members` --
identity's tables, queried from a file in neither module. A schema change in
identity would have broken connectors through a third package that named itself
after both.

Identity answers both questions now (`identity/contracts/organizations.py`), and
answers the second one as a *role* rather than as a yes/no, which is what leaves
the allowed-roles policy here where the port declares it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import (
    SYSTEM_ROLE_PERMISSIONS,
    equivalent_permission_ids,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.ports import OrganizationAccessPort
from app.modules.identity.contracts.organizations import (
    organization_exists as identity_organization_exists,
    organization_member_role,
)


class SqlAlchemyOrganizationAccessAdapter(OrganizationAccessPort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def organization_exists(self, organization_id: UUID) -> bool:
        return await identity_organization_exists(self._uow, organization_id)

    async def user_has_organization_role(
        self, user_id: UUID, organization_id: UUID
    ) -> bool:
        role = await organization_member_role(
            self._uow, user_id=user_id, organization_id=organization_id
        )
        return role is not None

    async def user_holds_organization_permission(
        self, user_id: UUID, organization_id: UUID, permission_id: str
    ) -> bool:
        # The permission, not a list of role names: a role that gains or loses
        # it is answered here without a second list to keep in step (P8).
        role = await organization_member_role(
            self._uow, user_id=user_id, organization_id=organization_id
        )
        if role is None:
            return False
        held = SYSTEM_ROLE_PERMISSIONS.get(role.value, frozenset())
        return bool(equivalent_permission_ids(permission_id) & held)
