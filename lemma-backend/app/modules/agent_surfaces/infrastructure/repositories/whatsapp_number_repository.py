"""Reading and writing the WhatsApp number pool.

The pool is inventory: rows that say what the deployment owns. Who *holds* a
number is a fact about `agent_surfaces`, not about these rows, so this
repository never writes a holder -- it finds a number an organisation may take
and lets the caller's own write be the thing that takes it. See
:meth:`WhatsAppNumberRepository.allocate_for_organization`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
    WhatsAppNumberRole,
    WhatsAppNumberStatus,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.whatsapp_pool_models import (
    WhatsAppNumber,
)

#: What the caller does with a number to take it: whatever write makes this
#: organisation the holder -- in practice creating or updating the surface that
#: carries `surface_identity_id`. It runs inside a savepoint, and raising
#: `IntegrityError` from it means "somebody else took this one", which is an
#: answer rather than a failure.
NumberClaim = Callable[[WhatsAppNumberEntity], Awaitable[None]]

#: How many candidates one allocation will try before giving up. A pool is
#: inventory measured in dozens, and every attempt past the first means a
#: concurrent allocation beat this one to a number -- so this is a bound on a
#: pathological race, not on the pool. It also keeps the candidate read bounded,
#: which `scripts/check_unbounded_reads.py` requires and a pool this small never
#: notices.
_MAX_ALLOCATION_ATTEMPTS = 25

#: The admin tool's ceiling. A deployment that owns more numbers than this has
#: outgrown a list.
_MAX_POOL_PAGE = 500


class WhatsAppNumberRepository:
    """The `surface_whatsapp_numbers` table, as entities."""

    def __init__(self, uow: IUnitOfWork):
        self.uow = uow
        # Annotated as the async session it actually is. The repositories beside
        # this one write `Session`, which type-checks every `await` on it as an
        # error -- harmless only because nothing checks those files.
        self.session: AsyncSession = uow.session

    async def get_by_phone_number_id(
        self, phone_number_id: str
    ) -> WhatsAppNumberEntity | None:
        """The number an inbound delivery or a Graph call names."""
        stmt = select(WhatsAppNumber).where(
            WhatsAppNumber.phone_number_id == phone_number_id
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def shared_number(self) -> WhatsAppNumberEntity | None:
        """The one SHARED line, or None when the deployment declares none.

        None is ordinary and means "fall back to settings": a deployment with a
        single number has no rows at all, which is the state every deployment
        starts in.

        `uq_whatsapp_number_shared` makes "the one" true in the schema, so this
        does not have to choose between two.
        """
        stmt = select(WhatsAppNumber).where(
            WhatsAppNumber.role == WhatsAppNumberRole.SHARED.value
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return model.to_entity() if model else None

    async def list_all(
        self, *, limit: int = _MAX_POOL_PAGE
    ) -> list[WhatsAppNumberEntity]:
        """Every number the deployment owns, for whoever administers the pool.

        Retired numbers included: "what do we own" and "what may be handed out"
        are different questions, and this is the first one.
        """
        stmt = (
            select(WhatsAppNumber)
            .order_by(WhatsAppNumber.created_at, WhatsAppNumber.id)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]

    async def allocate_for_organization(
        self,
        *,
        organization_id: UUID,
        claim: NumberClaim,
    ) -> WhatsAppNumberEntity | None:
        """Give this organisation a number it does not already hold.

        ``None`` when the pool has nothing free for it -- and unlike the email
        allocator this is modelled on, **that is a normal answer, not a
        degenerate one**. ``_insert_on_first_free_address`` *generates* its
        candidates, so exhausting them means five random suffixes all collided
        and something is wrong; it logs `.degraded` and says so. A number pool
        has finite inventory bought one number at a time, so "every allocatable
        number is already held by someone" is a steady state a deployment can
        sit in for weeks, reached by ordinary success rather than by failure.
        So it is returned plainly and not logged here: the caller is the one
        that knows whether it is about to fail a person's request (say so) or
        offer the shared line instead (do not).

        Insert and retry rather than check then insert, exactly as the email
        allocator does. The candidate read below is a snapshot, and between it
        and the claim another request can take the same number; the arbiter is
        `uq_agent_org_whatsapp_number` on `agent_surfaces`, and a pre-check
        would still race. So the caller's own write decides, and an
        `IntegrityError` from it means "taken -- try the next one".

        Each attempt gets its own savepoint. A unique violation aborts a
        Postgres transaction outright, so without one the second attempt would
        raise `PendingRollbackError` and the caller's whole unit of work -- a
        surface being created, an onboarding half-finished -- would be lost to a
        race that has a perfectly good answer.

        Why the claim is the caller's and not this repository's: nothing on
        these rows records a holder, deliberately (see the migration). The
        holder is `agent_surfaces.organization_id` + `surface_identity_id`, and
        writing a surface is not this table's business -- so an allocation this
        repository performed alone would be one that nothing had taken.
        """
        candidates = await self._allocatable_for(organization_id)
        for number in candidates:
            try:
                async with self.session.begin_nested():
                    await claim(number)
            except IntegrityError:
                # Somebody else holds this number for this organisation now.
                # The savepoint rolled back, so the caller's transaction is
                # still usable and the next candidate is a real attempt.
                continue
            return number
        return None

    async def _allocatable_for(
        self, organization_id: UUID
    ) -> list[WhatsAppNumberEntity]:
        """Numbers that may be handed to this organisation, oldest first.

        "May" is three predicates: allocatable at all, not retired, and not
        already held here. The third is answered by `agent_surfaces`, because
        that is where holding is recorded -- and the join is spelled to match
        `uq_agent_org_whatsapp_number` exactly, so what this offers and what
        the index will accept cannot drift apart.

        An anti-join rather than `NOT IN (SELECT ...)`: the two say the same
        thing here, but the join keeps this to one statement with one `LIMIT`,
        which is the shape `scripts/check_unbounded_reads.py` can see is
        bounded -- a function building a second `select` reads to it as one
        bounded statement beside one that might not be. It also avoids `NOT
        IN`'s standing trap, where a single NULL in the subquery makes the
        whole predicate unknown and the result empty; the equality in the ON
        clause cannot match a NULL, so the guard `NOT IN` needs is not a guard
        this has to remember.

        No row multiplication to worry about: the unique index means at most one
        surface per (organisation, number).
        """
        already_held = and_(
            AgentSurface.organization_id == organization_id,
            AgentSurface.surface_type == SurfacePlatform.WHATSAPP.value,
            AgentSurface.surface_identity_id == WhatsAppNumber.phone_number_id,
        )
        stmt = (
            select(WhatsAppNumber)
            .outerjoin(AgentSurface, already_held)
            .where(
                AgentSurface.id.is_(None),
                WhatsAppNumber.role == WhatsAppNumberRole.ALLOCATABLE.value,
                WhatsAppNumber.status == WhatsAppNumberStatus.AVAILABLE.value,
            )
            # Oldest first: a number that has been in the pool longest is the
            # one a person is least likely to have just given up, so reuse is
            # least likely to look like a number changing hands mid-conversation.
            .order_by(WhatsAppNumber.created_at, WhatsAppNumber.id)
            .limit(_MAX_ALLOCATION_ATTEMPTS)
        )
        result = await self.session.execute(stmt)
        # Mapped to entities before any claim runs: a failed attempt rolls a
        # savepoint back, and reading an ORM instance loaded inside it afterwards
        # would re-fetch it in the middle of the retry loop.
        return [model.to_entity() for model in result.scalars().all()]

    async def create(self, entity: WhatsAppNumberEntity) -> WhatsAppNumberEntity:
        """Add a number to the pool, secrets encrypted at rest."""
        cipher = get_secret_cipher()
        model = WhatsAppNumber(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            phone_number_id=entity.phone_number_id,
            display_phone_number=entity.display_phone_number,
            waba_id=entity.waba_id,
            access_token=cipher.encrypt_str(entity.access_token),
            app_secret=cipher.encrypt_str(entity.app_secret),
            verify_token=cipher.encrypt_str(entity.verify_token),
            onboarding_email_flow_id=entity.onboarding_email_flow_id,
            onboarding_code_flow_id=entity.onboarding_code_flow_id,
            role=entity.role.value,
            status=entity.status.value,
            notes=entity.notes,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    async def retire(self, phone_number_id: str) -> WhatsAppNumberEntity | None:
        """Stop handing this number out, without disturbing whoever holds it.

        The row stays and the holder keeps it: retiring is a decision about
        future allocations. `remove` is the other half -- "we no longer own
        this" -- and conflating the two loses the ability to say the first.
        """
        model = await self._model_for(phone_number_id)
        if model is None:
            return None
        model.status = WhatsAppNumberStatus.RETIRED.value
        await self.session.flush()
        return model.to_entity()

    async def remove(self, phone_number_id: str) -> bool:
        """Drop a number the deployment no longer owns. False when it was gone.

        Nothing cascades: `agent_surfaces.surface_identity_id` is a string, not
        a foreign key onto this table, so a surface still naming this number
        keeps naming it. That is the honest outcome -- the number is gone from
        Meta's side too -- and it is the caller's job to deal with the surfaces,
        which is why this is `remove` and `retire` exists beside it.
        """
        model = await self._model_for(phone_number_id)
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def _model_for(self, phone_number_id: str) -> WhatsAppNumber | None:
        stmt = select(WhatsAppNumber).where(
            WhatsAppNumber.phone_number_id == phone_number_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
