"""AccountRepository's IntegrityError -> AccountAlreadyConnectedError translation.

App-level dedup (ConnectorService._reject_if_identity_already_connected)
rejects the common case before create/update ever runs, but that
check-then-act has a TOCTOU gap under concurrency. These tests cover the
repository-level backstop directly against a stubbed session so they don't
need a real Postgres connection."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.errors import AccountAlreadyConnectedError
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
)
from app.modules.vault.contracts import Revealed, SecretKind, SecretRef

pytestmark = pytest.mark.asyncio


class _FakeResult:
    rowcount = 1

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

    def collect_events(self, events):
        return None


class _FakeVault:
    """Records what the repository asks of the vault; holds JSON secrets."""

    def __init__(self, held: dict | None = None):
        self.held = dict(held or {})
        self.calls: list[tuple[str, dict]] = []

    async def put(self, **kwargs):
        self.calls.append(("put", kwargs))
        ref = SecretRef(uuid4(), 1)
        self.held[ref.id] = kwargs["value"]
        return ref

    async def replace(self, secret_id, **kwargs):
        self.calls.append(("replace", {"secret_id": secret_id, **kwargs}))
        self.held[secret_id] = kwargs["value"]
        return SecretRef(secret_id, 2)

    async def reveal(self, secret_id, **kwargs):
        self.calls.append(("reveal", {"secret_id": secret_id, **kwargs}))
        return _revealed(secret_id, self.held[secret_id])

    async def reveal_many(self, expected, *, purpose):
        return {
            secret_id: _revealed(secret_id, self.held[secret_id])
            for secret_id in expected
            if secret_id in self.held
        }


def _revealed(secret_id, value) -> Revealed:
    import json

    return Revealed(
        secret_id, 1, SecretKind.JSON, None, json.dumps(value).encode("utf-8")
    )


def _repo(session, vault: _FakeVault | None = None) -> AccountRepository:
    return AccountRepository(uow=_FakeUow(session), vault=vault or _FakeVault())


def _located(secret_id=None, credentials=None):
    """The columns the credential store selects to find an account's secret."""
    return SimpleNamespace(
        credentials_secret_id=secret_id,
        organization_id=uuid4(),
        user_id=uuid4(),
        credentials=credentials,
    )


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
    repo = _repo(session)

    with pytest.raises(AccountAlreadyConnectedError):
        await repo.create(_account_entity())


async def test_write_model_ignores_read_side_connector_relationship():
    session = _FakeSession()
    repo = _repo(session)

    model = repo._to_model(_account_entity(connector=None))

    assert model.connector_id == "asana"
    assert "connector" not in model.__dict__
    # The credentials go to the vault, never onto the row.
    assert model.credentials is None
    assert model.credentials_secret_id is None


async def test_create_reraises_unrelated_integrity_error():
    session = _FakeSession(flush_exc=_unrelated_error())
    repo = _repo(session)

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
    repo = _repo(session)

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
    repo = _repo(session)

    with pytest.raises(IntegrityError):
        await repo.update(_account_entity(id=existing.id))


async def test_create_stores_the_credentials_in_the_vault_and_points_at_them():
    # Stopped at the insert: what matters is what was added, and with what.
    session = _FakeSession(flush_exc=_unrelated_error())
    vault = _FakeVault()
    entity = _account_entity()

    with pytest.raises(IntegrityError):
        await _repo(session, vault).create(entity)

    ((verb, put),) = [call for call in vault.calls if call[0] == "put"]
    assert put["purpose"] == ACCOUNT_CREDENTIALS_PURPOSE
    assert put["scope"].organization_id == entity.organization_id
    assert put["scope"].user_id == entity.user_id
    assert put["value"]["access_token"] == "tok"
    (row,) = session.added
    assert row.credentials_secret_id in vault.held
    assert row.credentials is None


class TestWritesThatLeaveTheCredentialsAlone:
    """An entity is a copy made when it was read. `update` once wrote the
    credentials back from it, and marking or binding an account from a copy
    read before a token refresh put the old token back -- a spent one, where
    refresh tokens are single-use. Credentials are written only on purpose."""

    async def test_update_never_writes_the_credentials(self):
        from app.modules.connectors.infrastructure.models import Account

        existing = Account(
            id=uuid4(),
            user_id=uuid4(),
            organization_id=uuid4(),
            auth_config_id=uuid4(),
            connector_id="asana",
            status="CONNECTED",
            is_default=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session = _FakeSession(existing=existing)
        vault = _FakeVault()

        await _repo(session, vault).update(
            _account_entity(id=existing.id, credentials={"access_token": "stale"})
        )

        assert [verb for verb, _ in vault.calls if verb != "reveal"] == []
        assert existing.credentials is None
        assert existing.credentials_secret_id is None

    async def test_marking_an_account_writes_its_status_and_nothing_else(self):
        session = _FakeSession()
        repo = _repo(session)

        await repo.set_status(uuid4(), AccountStatus.REAUTH_REQUIRED)

        (statement,) = session.statements
        sql = str(statement)
        assert sql.startswith("UPDATE accounts SET status=")
        assert "credentials" not in sql
        assert statement.compile().params["status"] == "REAUTH_REQUIRED"

    async def test_binding_an_account_writes_its_routing_key_and_nothing_else(self):
        session = _FakeSession()
        repo = _repo(session)

        await repo.set_external_ref(uuid4(), "158040062")

        (statement,) = session.statements
        sql = str(statement)
        assert sql.startswith("UPDATE accounts SET external_ref=")
        assert "credentials" not in sql

    async def test_the_stored_credentials_are_read_as_a_column(self):
        """Not as an entity: while the session still holds an instance, an
        entity read answers with it, and that is the copy being looked behind."""
        secret_id = uuid4()
        located = _located(secret_id)
        session = _FakeSession(existing=located)
        vault = _FakeVault({secret_id: {"access_token": "new"}})

        stored = await _repo(session, vault).stored_credentials(uuid4())

        assert stored == {"access_token": "new"}
        ((_, reveal),) = vault.calls
        assert reveal["purpose"] == ACCOUNT_CREDENTIALS_PURPOSE
        assert reveal["expect"].user_id == located.user_id
        (statement,) = session.statements
        sql = str(statement)
        assert sql.startswith("SELECT accounts.credentials_secret_id")
        # Locked, so a decision made from the answer cannot be overtaken by a
        # refresh that commits a moment later.
        assert sql.rstrip().endswith("FOR UPDATE")

    async def test_a_refreshed_credential_always_says_the_account_is_connected(self):
        """The status is in the statement whether or not this session saw it
        change: a refresh that lost a race may have marked the account since,
        and the ORM writes a status only when its own copy says it changed."""
        secret_id = uuid4()
        session = _FakeSession(existing=_located(secret_id))
        vault = _FakeVault({secret_id: {"access_token": "old"}})

        await _repo(session, vault).store_refreshed(
            uuid4(), {"access_token": "new"}, expected_version=3
        )

        ((_, replaced),) = vault.calls
        assert replaced["value"] == {"access_token": "new"}
        assert replaced["expected_version"] == 3
        _locate, statement = session.statements
        params = statement.compile().params
        assert params["status"] == "CONNECTED"
        # The old column is cleared in the same statement, so a process on the
        # previous release reading the row fails closed.
        assert params["credentials"] is None
        # And nothing an older copy of the account could get wrong.
        assert {"external_ref", "email", "display_name"}.isdisjoint(params)

    async def test_a_credential_only_in_the_old_column_moves_into_the_vault(self):
        session = _FakeSession(existing=_located(None, {"access_token": "old"}))
        vault = _FakeVault()

        await _repo(session, vault).replace_credentials(
            uuid4(), {"access_token": "new"}
        )

        ((verb, put),) = vault.calls
        assert verb == "put"
        _locate, statement = session.statements
        params = statement.compile().params
        assert params["credentials"] is None
        assert params["credentials_secret_id"] in vault.held
        # Only if nobody else pointed it at a secret meanwhile.
        assert "credentials_secret_id IS NULL" in str(statement)

    async def test_an_account_that_is_gone_has_no_stored_credentials(self):
        session = _FakeSession(existing=None)
        repo = _repo(session)

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
        repo = _repo(session)

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
