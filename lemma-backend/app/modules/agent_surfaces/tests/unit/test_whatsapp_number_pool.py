"""The number pool's data layer: what it offers, and what it refuses to offer.

Allocation is the interesting half. It hands out a scarce, finite thing to
organisations that keep it, so the two failures worth guarding are opposites:
handing one number to an organisation twice, and reporting a pool as empty when
it is not. The database is the arbiter of the first -- these compile the
statement to check it asks the right question, and drive the retry loop to check
it believes the answer.

Stubbed session rather than Postgres so they stay in the unit lane, the same way
``test_conversation_read_narrowing`` and ``test_account_repository`` do.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.core.crypto import get_secret_cipher
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
    WhatsAppNumberStatus,
)
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)
from app.modules.agent_surfaces.infrastructure.whatsapp_pool_models import (
    WhatsAppNumber,
)
from app.modules.test_support.mappers import configure_test_mappers

# Compiling a statement configures the mappers, and a partial model graph fails
# to resolve its relationship targets by name -- so without this the file passes
# in a suite and fails on its own.
configure_test_mappers()

pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Savepoint:
    """``session.begin_nested()``: rolls back on the way out when it raises."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._session.savepoint_rollbacks += 1
        # False: the exception keeps going, exactly as a real savepoint lets it.
        return False


class _Session:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.statements: list[object] = []
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.savepoints = 0
        self.savepoint_rollbacks = 0

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self._rows)

    def begin_nested(self):
        self.savepoints += 1
        return _Savepoint(self)

    def add(self, instance):
        self.added.append(instance)

    async def delete(self, instance):
        self.deleted.append(instance)

    async def flush(self):
        return None


class _Uow:
    def __init__(self, rows=()):
        self.session = _Session(rows)


def _row(
    phone_number_id: str,
    *,
    status: WhatsAppNumberStatus = WhatsAppNumberStatus.AVAILABLE,
    access_token: str | None = None,
) -> WhatsAppNumber:
    """A detached pool row, complete enough for ``to_entity()`` to run."""
    now = datetime.now(timezone.utc)
    return WhatsAppNumber(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        phone_number_id=phone_number_id,
        display_phone_number=f"+1555{phone_number_id}",
        waba_id="waba-1",
        access_token=access_token,
        status=status.value,
    )


class _PostgresError(Exception):
    """asyncpg's shape: the constraint it refused on, named on the exception."""

    def __init__(self, constraint_name: str):
        super().__init__(f"duplicate key value violates {constraint_name}")
        self.constraint_name = constraint_name


def _violation(constraint_name: str) -> IntegrityError:
    """What the caller's claim raises when the database refuses its write.

    Built with a named constraint rather than a bare message because that is the
    only thing separating "somebody took this number" from every other integrity
    failure a surface write can produce, and the retry loop now reads it.
    """
    return IntegrityError("INSERT", {}, _PostgresError(constraint_name))


_NUMBER_TAKEN = "uq_agent_org_whatsapp_number"


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


async def test_the_cold_open_line_is_the_oldest_number_still_available():
    """No row claims to be the shared line; the oldest available one answers.

    A `SHARED` flag was a second place for "which number do we send from" to be
    recorded, and two places that can disagree about one fact is the shape of
    every bug this table was drawn to avoid. So the question is answered by
    ordering rather than by a claim -- which also makes it deterministic, so two
    replicas asked a second apart answer the same.

    Retired is excluded, and that is the half worth pinning: retiring a number
    means stop using it, and a cold open is a use. A query that ordered without
    filtering would send identity's verification codes from a number the
    deployment has given up.
    """
    uow = _Uow([_row("oldest-1")])
    repository = WhatsAppNumberRepository(uow)

    found = await repository.oldest_available_number()

    assert found is not None
    assert found.phone_number_id == "oldest-1"
    sql = _sql(uow.session.statements[0])
    assert "status = 'AVAILABLE'" in sql
    assert "ORDER BY surface_whatsapp_numbers.created_at" in sql
    assert "LIMIT 1" in sql


async def test_a_deployment_with_no_rows_has_no_cold_open_line_of_its_own():
    """Absent is ordinary: it means "fall back to settings", which is the state
    every one-number deployment is already in and must keep working from."""
    repository = WhatsAppNumberRepository(_Uow())

    assert await repository.oldest_available_number() is None


async def test_secrets_are_encrypted_on_the_way_in_and_readable_on_the_way_out():
    """The column holds an envelope; the entity holds the token.

    Written as a round trip rather than as "encrypt was called" because the
    failure that matters is asymmetric: a write that skips encryption stores a
    Meta access token in plaintext, and a read that skips decryption hands the
    envelope to the Graph API, where it fails as a bad credential rather than as
    a bug in this file.
    """
    entity = WhatsAppNumberEntity(
        phone_number_id="pn-1",
        display_phone_number="+15551234567",
        waba_id="waba-1",
        access_token="EAAG-secret-token",
        app_secret="app-secret",
        verify_token="verify-secret",
    )
    uow = _Uow()
    repository = WhatsAppNumberRepository(uow)

    stored = await repository.create(entity)

    model = uow.session.added[0]
    for column, plaintext in (
        ("access_token", "EAAG-secret-token"),
        ("app_secret", "app-secret"),
        ("verify_token", "verify-secret"),
    ):
        written = getattr(model, column)
        assert written != plaintext, f"{column} reached the column in plaintext"
        assert get_secret_cipher().decrypt_str(written) == plaintext
    assert stored.access_token == "EAAG-secret-token"
    assert stored.app_secret == "app-secret"
    assert stored.verify_token == "verify-secret"


async def test_a_number_this_organisation_already_holds_is_not_offered():
    """The first of the two defences, and the one that avoids the retry.

    Holding is recorded on the surface, not on the pool row, so "does this
    organisation already have one" is a question about ``agent_surfaces`` -- and
    the exclusion has to be spelled the way ``uq_agent_org_whatsapp_number`` is,
    or the query offers a number the index will then refuse and every allocation
    pays for a round trip that could never have succeeded.
    """
    organization_id = uuid4()
    uow = _Uow([_row("pn-1")])
    repository = WhatsAppNumberRepository(uow)

    async def _claim(_number):
        return None

    await repository.allocate_for_organization(
        organization_id=organization_id, claim=_claim
    )

    sql = _sql(uow.session.statements[0])
    assert "LEFT OUTER JOIN agent_surfaces" in sql
    assert f"agent_surfaces.organization_id = '{organization_id}'" in sql
    assert "agent_surfaces.surface_type = 'WHATSAPP'" in sql
    assert (
        "agent_surfaces.surface_identity_id = surface_whatsapp_numbers.phone_number_id"
        in sql
    )
    # The anti-join half: keep only the numbers that found no holder.
    assert "agent_surfaces.id IS NULL" in sql
    # The pool half: not one that was retired.
    assert "status = 'AVAILABLE'" in sql


async def test_a_number_taken_between_the_read_and_the_claim_moves_to_the_next():
    """The race the retry exists for, answered by the index rather than a check.

    The candidate read is a snapshot. Another request can claim the same number
    before this one writes, and the only thing that knows is the unique index --
    which reports it as an ``IntegrityError`` from the caller's own write. So a
    failed claim must mean "try the next one", not "the pool is empty".
    """
    claimed: list[str] = []

    async def _claim(number):
        claimed.append(number.phone_number_id)
        if number.phone_number_id == "pn-1":
            raise _violation(_NUMBER_TAKEN)

    uow = _Uow([_row("pn-1"), _row("pn-2")])
    repository = WhatsAppNumberRepository(uow)

    allocated = await repository.allocate_for_organization(
        organization_id=uuid4(), claim=_claim
    )

    assert allocated is not None
    assert allocated.phone_number_id == "pn-2"
    assert claimed == ["pn-1", "pn-2"]
    # One savepoint per attempt, and only the failed one rolled back. Without
    # the savepoint the unique violation aborts the whole Postgres transaction
    # and the second attempt cannot run at all.
    assert uow.session.savepoints == 2
    assert uow.session.savepoint_rollbacks == 1


async def test_an_exhausted_pool_is_an_answer_and_not_an_error():
    """Finite inventory, so "nothing free" is a steady state, not a defect.

    The email allocator this is modelled on *generates* its candidates, so
    running out means five random suffixes collided and it logs ``.degraded``.
    A pool is bought one number at a time and can legitimately be fully
    allocated for weeks -- so this returns ``None`` plainly, and leaves the
    caller to decide whether that is a person's request failing or a fallback to
    the shared line.
    """
    calls: list[str] = []

    async def _claim(number):
        calls.append(number.phone_number_id)

    repository = WhatsAppNumberRepository(_Uow())

    allocated = await repository.allocate_for_organization(
        organization_id=uuid4(), claim=_claim
    )

    assert allocated is None
    assert calls == [], "nothing to claim, so nothing should have been written"


async def test_every_candidate_lost_to_a_race_reads_as_exhausted_too():
    """Same answer, arrived at the other way -- and the caller's transaction
    survives it: each attempt rolled back its own savepoint and no more."""

    async def _claim(_number):
        raise _violation(_NUMBER_TAKEN)

    uow = _Uow([_row("pn-1"), _row("pn-2")])
    repository = WhatsAppNumberRepository(uow)

    allocated = await repository.allocate_for_organization(
        organization_id=uuid4(), claim=_claim
    )

    assert allocated is None
    assert uow.session.savepoints == 2
    assert uow.session.savepoint_rollbacks == 2


async def test_the_candidate_read_is_bounded():
    """A pool is inventory measured in dozens, and the read says so.

    Not a performance nicety: an unbounded read here is what
    ``scripts/check_unbounded_reads.py`` exists to stop, and the ceiling is on
    *attempts* -- every one past the first means a lost race, so a pool that
    needs more than this has a problem no amount of retrying fixes.
    """
    uow = _Uow()
    repository = WhatsAppNumberRepository(uow)

    async def _claim(_number):
        return None

    await repository.allocate_for_organization(organization_id=uuid4(), claim=_claim)

    assert "LIMIT" in _sql(uow.session.statements[0])


async def test_retiring_a_number_leaves_the_row_and_its_holder_alone():
    """ "Stop handing this out" and "we no longer own it" are different facts.

    Retiring must not remove the row: whoever holds the number still has to
    resolve it, and a deleted row is a webhook arriving for a number the
    deployment cannot name.
    """
    row = _row("pn-1")
    uow = _Uow([row])
    repository = WhatsAppNumberRepository(uow)

    retired = await repository.retire("pn-1")

    assert retired is not None
    assert retired.status is WhatsAppNumberStatus.RETIRED
    assert uow.session.deleted == []


async def test_removing_a_number_that_is_already_gone_says_so():
    """Idempotent rather than raising: the admin tool's second attempt after a
    timeout is the common case, not an error."""
    repository = WhatsAppNumberRepository(_Uow())

    assert await repository.remove("pn-1") is False


async def test_an_integrity_failure_that_is_not_the_number_reaches_the_caller():
    """Only ``uq_agent_org_whatsapp_number`` means "taken". Nothing else does.

    The claim is a whole surface write, so it can violate the one-surface-per-
    agent index, the composite organisation foreign key, or the pod-unique name
    -- none of which the next candidate would fix. Swallowing them retried
    twenty-five times and then answered ``None``, which the caller turns into a
    503 telling the operator to buy numbers they already have, for a bug that
    had nothing to do with the pool.

    The savepoint still rolled back, so what reaches the caller is the real
    cause on a transaction it can still use.
    """
    uow = _Uow([_row("pn-1"), _row("pn-2")])
    repository = WhatsAppNumberRepository(uow)
    attempts: list[str] = []

    async def _claim(number):
        attempts.append(number.phone_number_id)
        raise _violation("uq_agent_surface_agent_type")

    with pytest.raises(IntegrityError):
        await repository.allocate_for_organization(
            organization_id=uuid4(), claim=_claim
        )

    assert attempts == ["pn-1"], "an unrelated failure must not walk the pool"
    assert uow.session.savepoint_rollbacks == 1


async def test_an_integrity_failure_the_database_did_not_name_reaches_the_caller():
    """Silence is not consent. An unnamed constraint is not the one we tolerate.

    Reading nothing and retrying anyway is the same bug in a quieter form: it
    reports whatever went wrong as an empty pool. Failing towards the caller
    means an unexpected driver shape shows up as itself.
    """
    repository = WhatsAppNumberRepository(_Uow([_row("pn-1")]))

    async def _claim(_number):
        raise IntegrityError("INSERT", {}, Exception("something else entirely"))

    with pytest.raises(IntegrityError):
        await repository.allocate_for_organization(
            organization_id=uuid4(), claim=_claim
        )
