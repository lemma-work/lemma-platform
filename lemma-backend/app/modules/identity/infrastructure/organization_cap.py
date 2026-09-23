"""Whether a person may make another organization.

The limit is on *making* organizations, so it is checked in one place:
`OrganizationService.create_organization`. What it counts is every
organization the person owns -- every ORG_OWNER membership, co-ownership
included -- under a lock on the person, so two made at once cannot both be
the last one allowed.

Being made an owner of somebody else's organization -- invited as one, or
promoted -- is deliberately not refused. That is the other organization
deciding, often one on a team plan, and refusing it would stop a team adding a
co-owner because of the co-owner's own plan. It does count against what they
may make afterwards.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, select, text

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.plan_limits import build_plan_limits
from app.modules.identity.domain.errors import OrganizationLimitError
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)


async def refuse_if_at_organization_limit(
    uow: SqlAlchemyUnitOfWork, user_id: UUID
) -> None:
    """Raise `OrganizationLimitError` if the person owns all their plan allows."""
    plan_limits = build_plan_limits(uow)
    if plan_limits is None:
        return
    limit = await plan_limits.organization_limit(user_id=user_id)
    if limit is None:
        return

    digest = hashlib.blake2b(
        str(user_id).encode(), digest_size=8, person=b"lemma-org-quota"
    ).digest()
    await uow.session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(digest, "big", signed=True)},
    )
    mark_transaction_scoped_lock(uow.session)
    owned = (
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.role == OrganizationRole.ORG_OWNER.value,
        )
    )
    used = int((await uow.session.execute(owned)).scalar_one())
    if used >= limit:
        raise OrganizationLimitError(limit=limit, used=used)
