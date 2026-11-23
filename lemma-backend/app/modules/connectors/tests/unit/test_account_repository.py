"""AccountRepository's IntegrityError -> AccountAlreadyConnectedError translation.

App-level dedup (ConnectorService._reject_if_identity_already_connected)
rejects the common case before create/update ever runs, but that
check-then-act has a TOCTOU gap under concurrency. These tests cover the
repository-level backstop directly against a stubbed session so they don't
need a real Postgres connection."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.errors import AccountAlreadyConnectedError
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)

pytestmark = pytest.mark.asyncio


class _FakeResult:
    def __init__(self, instance):
        self._instance = instance

    def scalars(self):
        return self

    def first(self):
        return self._instance

    def scalar_one_or_none(self):
        return self._instance


class _FakeSession:
    def __init__(self, *, flush_exc: Exception | None = None, existing=None):
        self._flush_exc = flush_exc
        self._existing = existing
        self.added = []
        self.statements = []

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        if self._flush_exc is not None:
            raise self._flush_exc

    async def refresh(self, instance, attribute_names=None):
        return None

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._existing)


class _FakeUow:
    def __init__(self, session):
        self.session = session


class _NoopEncryption:
    def encrypt_json(self, value):
        return value

    def decrypt_json(self, value):
        return value

    async def encrypt_json_async(self, value):
        return value

    async def decrypt_json_async(self, value):
        return value


def _account_entity(**overrides) -> AccountEntity:
    defaults = {
        "user_id": uuid4(),
        "organization_id": uuid4(),
        "auth_config_id": uuid4(),
        "connector_id": "asana",
        "status": AccountStatus.CONNECTED,
        "provider_account_id": "acc-1",
        "credentials": {"access_token": "tok"},
    }
    defaults.update(overrides)
    return AccountEntity(**defaults)


def _duplicate_identity_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO accounts ...",
        {},
        Exception(
            "duplicate key value violates unique constraint "
            '"uq_accounts_provider_identity"'
        ),
    )


def _unrelated_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO accounts ...",
        {},
        Exception(
            "duplicate key value violates unique constraint "
            '"uq_accounts_default_per_auth_config"'
        ),
    )


async def test_create_translates_duplicate_identity_violation():
    session = _FakeSession(flush_exc=_duplicate_identity_error())
    repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

    with pytest.raises(AccountAlreadyConnectedError):
        await repo.create(_account_entity())


async def test_write_model_ignores_read_side_connector_relationship():
    session = _FakeSession()
    repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

    model = await repo._to_model(_account_entity(connector=None))

    assert model.connector_id == "asana"
    assert "connector" not in model.__dict__


async def test_create_reraises_unrelated_integrity_error():
    session = _FakeSession(flush_exc=_unrelated_error())
    repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

    with pytest.raises(IntegrityError):
        await repo.create(_account_entity())


async def test_update_translates_duplicate_identity_violation():
    from app.modules.connectors.infrastructure.models import Account

    existing = Account(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        connector_id="asana",
        status="CONNECTED",
        credentials={"access_token": "tok"},
    )
    session = _FakeSession(flush_exc=_duplicate_identity_error(), existing=existing)
    repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

    with pytest.raises(AccountAlreadyConnectedError):
        await repo.update(_account_entity(id=existing.id))


async def test_update_reraises_unrelated_integrity_error():
    from app.modules.connectors.infrastructure.models import Account

    existing = Account(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        connector_id="asana",
        status="CONNECTED",
        credentials={"access_token": "tok"},
    )
    session = _FakeSession(flush_exc=_unrelated_error(), existing=existing)
    repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

    with pytest.raises(IntegrityError):
        await repo.update(_account_entity(id=existing.id))


class TestWritesThatLeaveTheCredentialsAlone:
    """`update` writes the whole row from the entity it is handed, credentials
    included, and an entity is a copy made when it was read. Marking an
    account, or binding it, from a copy read before a token refresh put the
    old token back -- a spent one, where refresh tokens are single-use."""

    async def test_marking_an_account_writes_its_status_and_nothing_else(self):
        session = _FakeSession()
        repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

        await repo.set_status(uuid4(), AccountStatus.REAUTH_REQUIRED)

        (statement,) = session.statements
        sql = str(statement)
        assert sql.startswith("UPDATE accounts SET status=")
        assert "credentials" not in sql
        assert statement.compile().params["status"] == "REAUTH_REQUIRED"

    async def test_binding_an_account_writes_its_routing_key_and_nothing_else(self):
        session = _FakeSession()
        repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

        await repo.set_external_ref(uuid4(), "158040062")

        (statement,) = session.statements
        sql = str(statement)
        assert sql.startswith("UPDATE accounts SET external_ref=")
        assert "credentials" not in sql

    async def test_the_stored_credentials_are_read_as_a_column(self):
        """Not as an entity: the session answers an entity read with the
        instance it already holds, which is the copy being looked behind."""
        session = _FakeSession(existing={"access_token": "new"})
        repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

        assert await repo.stored_credentials(uuid4()) == {"access_token": "new"}

        (statement,) = session.statements
        assert str(statement).startswith("SELECT accounts.credentials")

    async def test_an_account_that_is_gone_has_no_stored_credentials(self):
        session = _FakeSession(existing=None)
        repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

        assert await repo.stored_credentials(uuid4()) is None


class TestEveryAccountReadLoadsItsConnectorEagerly:
    """`_to_entity` reads `instance.connector`, so a select that does not ask
    for it raises `MissingGreenlet` the moment it is used.

    Asserted on the statement rather than on a returned entity, because a
    stubbed session hands back a stand-in whose `connector` attribute answers
    happily -- which is precisely why this went unnoticed: `promote_next_default`
    was the one select in the file that omitted the option, and deleting an
    account that happened to be the default answered 500 in production while
    this suite stayed green.
    """

    @staticmethod
    def _asks_for_the_connector(stmt) -> bool:
        return any(
            "connector" in str(getattr(option, "path", option))
            for option in getattr(stmt, "_with_options", ())
        )

    async def test_promoting_the_next_default_asks_for_it(self):
        session = _FakeSession(existing=None)
        repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

        await repo.promote_next_default(
            user_id=uuid4(),
            auth_config_id=uuid4(),
            exclude_account_id=uuid4(),
        )

        assert session.statements, "the promotion has to query for a candidate"
        assert self._asks_for_the_connector(session.statements[0])

    async def test_the_repository_states_the_rule_once_for_every_select(self):
        """A select added later must not have to rediscover this."""
        import inspect

        source = inspect.getsource(AccountRepository)
        selects = source.count("select(Account)")
        eager = source.count("selectinload(Account.connector)")

        assert eager >= selects, (
            f"{selects} selects over Account but only {eager} ask for its "
            "connector; `_to_entity` reads it and will raise MissingGreenlet"
        )
