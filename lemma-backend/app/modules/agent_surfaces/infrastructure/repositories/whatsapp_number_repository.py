"""Reading and writing the WhatsApp number pool.

The pool is inventory: rows that say what the deployment owns. Who *holds* a
number is a fact about `agent_surfaces`, not about these rows, so this
repository never writes a holder -- it finds a number an organisation may take
and lets the caller's own write be the thing that takes it. See
:meth:`WhatsAppNumberRepository.allocate_for_organization`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
    WhatsAppNumberStatus,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.whatsapp_pool_models import (
    WhatsAppNumber,
    WhatsAppNumberSecrets,
)
from app.modules.vault.contracts import (
    JsonObject,
    SecretScope,
    Vault,
    vault_for,
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

#: The one constraint whose violation means "somebody else took this number".
#: Named here because the claim is a whole surface write, and a surface write can
#: violate several other things.
_NUMBER_TAKEN_CONSTRAINT = "uq_agent_org_whatsapp_number"

#: A number's three secrets live in the vault as one JSON object, holding only
#: the keys that are set. Deployment-scoped: the pool belongs to no
#: organisation, and a number is handed to several over its life.
CREDENTIALS_PURPOSE = "agent_surfaces.whatsapp_number.credentials"
_OWNER_TABLE = "surface_whatsapp_numbers"
_SECRET_KEYS = ("access_token", "app_secret", "verify_token")


def _credentials_value(entity: WhatsAppNumberEntity) -> JsonObject | None:
    """What to store for a new number, or None when it declares nothing.

    A missing key is not an empty value: it is "fall back to settings", the
    same answer a number with no secret at all gets. So an unset secret is left
    out rather than stored as an empty string that would shadow the setting.
    """
    value: JsonObject = {
        key: secret for key in _SECRET_KEYS if (secret := getattr(entity, key))
    }
    return value or None


def _secrets_from(stored: JsonObject | None) -> WhatsAppNumberSecrets:
    """The stored object as the three optional secrets; absent keys stay None."""
    if not stored:
        return WhatsAppNumberSecrets()

    def text(key: str) -> str | None:
        value = stored.get(key)
        return value if isinstance(value, str) else None

    return WhatsAppNumberSecrets(
        access_token=text("access_token"),
        app_secret=text("app_secret"),
        verify_token=text("verify_token"),
    )


def _violated_constraint(error: IntegrityError) -> str | None:
    """Which constraint the database refused on, if it said.

    Three places to look, because the driver stack moves it. SQLAlchemy's
    asyncpg dialect does not re-raise asyncpg's own `UniqueViolationError`: it
    translates it into a DBAPI-shaped `IntegrityError` carrying only `sqlstate`,
    and chains the original underneath. So `error.orig` is the translation and
    `error.orig.__cause__` is the exception that actually knows the name.
    `.diag` is psycopg's spelling, checked because the migration and test
    tooling reach Postgres through it.

    `None` means the database did not say. That is treated as "not the
    constraint we tolerate", which is the safe direction: a mysterious integrity
    failure reaches the caller instead of being retried twenty-five times and
    reported as an empty pool.
    """
    original = error.orig
    for candidate in (original, getattr(original, "__cause__", None)):
        named = getattr(candidate, "constraint_name", None)
        if isinstance(named, str) and named:
            return named
        named = getattr(getattr(candidate, "diag", None), "constraint_name", None)
        if isinstance(named, str) and named:
            return named
    return None


class WhatsAppNumberRepository:
    """The `surface_whatsapp_numbers` table, as entities."""

    def __init__(self, uow: IUnitOfWork, *, vault: Vault | None = None):
        self.uow = uow
        # Annotated as the async session it actually is. The repositories beside
        # this one write `Session`, which type-checks every `await` on it as an
        # error -- harmless only because nothing checks those files.
        self.session: AsyncSession = uow.session
        self._vault = vault or vault_for(self.session)

    async def _entities(
        self, models: Sequence[WhatsAppNumber]
    ) -> list[WhatsAppNumberEntity]:
        """Rows as entities, their secrets revealed in one vault read.

        Every read path comes through here, so an entity in hand always holds
        usable values -- the callers compare, sign and send with them and have
        no vault of their own. One `reveal_many` however many rows: a list of
        the pool is one query for the secrets, not one per number.
        """
        scopes = {
            model.credentials_secret_id: SecretScope.system()
            for model in models
            if model.credentials_secret_id is not None
        }
        revealed = await self._vault.reveal_many(scopes, purpose=CREDENTIALS_PURPOSE)

        def secrets_for(model: WhatsAppNumber) -> WhatsAppNumberSecrets:
            secret_id = model.credentials_secret_id
            found = revealed.get(secret_id) if secret_id is not None else None
            return _secrets_from(found.json() if found is not None else None)

        return [model.to_entity(secrets_for(model)) for model in models]

    async def _entity(
        self, model: WhatsAppNumber | None
    ) -> WhatsAppNumberEntity | None:
        return (await self._entities([model]))[0] if model is not None else None

    async def get_by_phone_number_id(
        self, phone_number_id: str
    ) -> WhatsAppNumberEntity | None:
        """The number an inbound delivery or a Graph call names."""
        stmt = select(WhatsAppNumber).where(
            WhatsAppNumber.phone_number_id == phone_number_id
        )
        result = await self.session.execute(stmt)
        return await self._entity(result.scalar_one_or_none())

    async def oldest_available_number(self) -> WhatsAppNumberEntity | None:
        """The pool's answer for the cold-open line, or None when it has none.

        The deployment's cold-open line -- what identity's phone verification
        sends from, and what a surface holding no number of its own answers with
        -- is the number in settings. This is the fallback for where settings
        are silent: a deployment that keeps its credentials in the pool and sets
        no `WHATSAPP_*` variables owns a line just as much, and answering "not
        configured" for it would leave verification switched off with working
        credentials sitting in a table.

        There is no row that *claims* to be the line, and deliberately so: a
        `SHARED` flag was a second place for the same fact to be wrong, and it
        made "which number do we send from" answerable two ways that could
        disagree. Oldest available instead -- deterministic, so two replicas
        answering the question a second apart answer it the same, and stable,
        because the oldest row is the one least likely to be the one just added.

        `RETIRED` is excluded because retirement means "stop using this number",
        and a cold open is a use. None is ordinary and means "fall back to
        settings": a deployment with a single number has no rows at all, which
        is the state every deployment starts in.
        """
        stmt = (
            select(WhatsAppNumber)
            .where(WhatsAppNumber.status == WhatsAppNumberStatus.AVAILABLE.value)
            .order_by(WhatsAppNumber.created_at, WhatsAppNumber.id)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return await self._entity(result.scalars().first())

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
        return await self._entities(result.scalars().all())

    async def any_allocatable(self) -> bool:
        """Does this deployment have a pool at all?

        The question separates two states that `allocate_for_organization`
        returning `None` cannot: a deployment that never added a number, and one
        whose numbers are all held. The first should keep behaving exactly as it
        did before there was a pool -- the shared line, no allocation, no error
        -- and the second is a genuine 503. Conflating them would either refuse
        every existing deployment or silently hand out the shared line to
        somebody who asked for a number of their own.
        """
        found = await self.session.scalar(
            select(WhatsAppNumber.id)
            .where(WhatsAppNumber.status == WhatsAppNumberStatus.AVAILABLE.value)
            .limit(1)
        )
        return found is not None

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
        `IntegrityError` naming `uq_agent_org_whatsapp_number` means "taken --
        try the next one". Any *other* constraint is re-raised: the claim writes
        a whole surface, so it can fail for reasons no other candidate would fix,
        and swallowing those turns a bug into a false report of an empty pool.

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
            except IntegrityError as error:
                if _violated_constraint(error) != _NUMBER_TAKEN_CONSTRAINT:
                    # Not a number being taken. The claim is a whole surface
                    # write, and it can violate `uq_agent_surface_agent_type`,
                    # the composite organisation foreign key, or the pod-unique
                    # name -- none of which another candidate can fix. Treating
                    # them all as "taken" retried twenty-five times and then
                    # reported an unrelated bug as pool exhaustion, which sends
                    # the operator to buy numbers they already have.
                    #
                    # The savepoint has rolled back either way, so this reaches
                    # the caller with a usable transaction and the real cause.
                    raise
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

        "May" is two predicates: not retired, and not already held here. The
        second is answered by `agent_surfaces`, because that is where holding is
        recorded -- and the join is spelled to match
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
        # would re-fetch it in the middle of the retry loop. Revealed here for
        # the same reason: the vault read happens once, outside every savepoint.
        return await self._entities(result.scalars().all())

    async def create(self, entity: WhatsAppNumberEntity) -> WhatsAppNumberEntity:
        """Add a number to the pool, its secrets (if any) as one vault secret.

        On this repository's transaction, so the secret and the row that owns it
        land together or not at all.
        """
        credentials = _credentials_value(entity)
        secret_id = None
        if credentials is not None:
            ref = await self._vault.put(
                scope=SecretScope.system(),
                purpose=CREDENTIALS_PURPOSE,
                value=credentials,
                owner_table=_OWNER_TABLE,
            )
            secret_id = ref.id
        model = WhatsAppNumber(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            phone_number_id=entity.phone_number_id,
            display_phone_number=entity.display_phone_number,
            waba_id=entity.waba_id,
            credentials_secret_id=secret_id,
            onboarding_email_flow_id=entity.onboarding_email_flow_id,
            onboarding_code_flow_id=entity.onboarding_code_flow_id,
            status=entity.status.value,
            notes=entity.notes,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity(_secrets_from(credentials))

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
        return await self._entity(model)

    async def remove(self, phone_number_id: str) -> bool:
        """Drop a number the deployment no longer owns. False when it was gone.

        Nothing cascades: `agent_surfaces.surface_identity_id` is a string, not
        a foreign key onto this table, so a surface still naming this number
        keeps naming it. That is the honest outcome -- the number is gone from
        Meta's side too -- and it is the caller's job to deal with the surfaces,
        which is why this is `remove` and `retire` exists beside it.

        The number's secret goes with the row: `vault_owned` on
        `credentials_secret_id` deletes it by trigger, whatever path the row
        takes out.
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
