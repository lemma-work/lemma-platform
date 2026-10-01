"""AccountRepository against a stubbed session and an in-memory vault.

Two things are covered. The IntegrityError -> AccountAlreadyConnectedError
translation: app-level dedup (ConnectorService._reject_if_identity_already_connected)
rejects the common case before create/update ever runs, but that check-then-act
has a TOCTOU gap under concurrency, and the repository is the backstop. And the
credential rules: credentials live in the vault, `update` never writes them, and
`replace_credentials` refuses a write based on a stale read.

The vault is injected through the constructor (`vault=`), not patched, so these
run the repository's real code against the vault's real refusals.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.errors import AccountAlreadyConnectedError
from app.modules.connectors.infrastructure.models import Account
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
    account_scope,
)
from app.modules.test_support.vault_fake import FakeVault
from app.modules.vault.contracts import SecretVersionConflict

pytestmark = pytest.mark.asyncio


class _FakeResult:
    def __init__(self, instance):
        self._instance = instance
        self.rowcount = 0 if instance is None else 1

    def scalars(self):
        return self

    def first(self):
        return self._instance

    def all(self):
        return [] if self._instance is None else list(self._instance)


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
        self.events = []

    def collect_events(self, events):
        self.events.extend(events)


class _CountingVault(FakeVault):
    """The fake vault, counting batch reads so a list can be held to one."""

    def __init__(self) -> None:
        super().__init__()
        self.batch_reads = 0

    async def reveal_many(self, expected, *, purpose):
        self.batch_reads += 1
        return await super().reveal_many(expected, purpose=purpose)


def _repo(session, vault: FakeVault | None = None) -> AccountRepository:
    return AccountRepository(uow=_FakeUow(session), vault=vault or FakeVault())


async def _stored_account(vault: FakeVault, credentials: dict) -> Account:
    """An account row whose credentials are already in ``vault``."""
    organization_id, user_id = uuid4(), uuid4()
    ref = await vault.put(
        scope=account_scope(organization_id, user_id),
        purpose=ACCOUNT_CREDENTIALS_PURPOSE,
        value=credentials,
        owner_table="accounts",
    )
    now = datetime.now(timezone.utc)
    return Account(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        auth_config_id=uuid4(),
        connector_id="github",
        status="CONNECTED",
        is_default=False,
        created_at=now,
        updated_at=now,
        credentials_secret_id=ref.id,
    )


def _written_entity(**overrides) -> AccountEntity:
    """An entity whose server-defaulted fields are set, as a flushed row has them.

    The stubbed session does not run the column defaults a real insert would,
    so the entity read back from the new row needs them stated.
    """
    now = datetime.now(timezone.utc)
    return _account_entity(
        **{
            "id": uuid4(),
            "is_default": False,
            "created_at": now,
            "updated_at": now,
            **overrides,
        }
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
    # The credentials are not a column; they reach the vault through `create`.
    assert model.credentials_secret_id is None


async def test_create_reraises_unrelated_integrity_error():
    session = _FakeSession(flush_exc=_unrelated_error())
    repo = _repo(session)

    with pytest.raises(IntegrityError):
        await repo.create(_account_entity())


async def test_update_translates_duplicate_identity_violation():
    existing = Account(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        connector_id="asana",
        status="CONNECTED",
    )
    session = _FakeSession(flush_exc=_duplicate_identity_error(), existing=existing)
    repo = _repo(session)

    with pytest.raises(AccountAlreadyConnectedError):
        await repo.update(_account_entity(id=existing.id))


async def test_update_reraises_unrelated_integrity_error():
    existing = Account(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        connector_id="asana",
        status="CONNECTED",
    )
    session = _FakeSession(flush_exc=_unrelated_error(), existing=existing)
    repo = _repo(session)

    with pytest.raises(IntegrityError):
        await repo.update(_account_entity(id=existing.id))


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


class TestCredentialsLiveInTheVault:
    async def test_create_puts_the_credentials_in_the_vault_scoped_to_the_owner(self):
        vault = FakeVault()
        repo = _repo(_FakeSession(), vault)
        entity = _written_entity(credentials={"access_token": "tok"})

        created = await repo.create(entity)

        (secret_id,) = vault.secrets
        secret = vault.secrets[secret_id]
        # Stored as the entity's serialized credential model, whole.
        assert secret.value["access_token"] == "tok"
        assert secret.purpose == ACCOUNT_CREDENTIALS_PURPOSE
        assert secret.scope == account_scope(entity.organization_id, entity.user_id)
        assert created.credentials_version == 1

    async def test_an_account_without_credentials_gets_no_secret(self):
        vault = FakeVault()
        repo = _repo(_FakeSession(), vault)

        created = await repo.create(_written_entity(credentials=None))

        assert vault.secrets == {}
        assert created.credentials is None
        assert created.credentials_version is None

    async def test_update_never_writes_the_credentials_it_was_handed(self):
        """The bug this closes: a caller that read the account before a refresh
        wrote the pre-refresh credentials back -- a spent single-use GitHub
        refresh token over the one the refresh had just stored."""
        vault = FakeVault()
        existing = await _stored_account(vault, {"refresh_token": "fresh"})
        repo = _repo(_FakeSession(existing=existing), vault)
        stale = _account_entity(
            id=existing.id,
            organization_id=existing.organization_id,
            user_id=existing.user_id,
            credentials={"refresh_token": "spent"},
            external_ref="installation-1",
        )

        updated = await repo.update(stale)

        assert vault.value_of(existing.credentials_secret_id) == {
            "refresh_token": "fresh"
        }
        assert vault.secrets[existing.credentials_secret_id].version == 1
        assert updated.external_ref == "installation-1"
        assert updated.credentials.model_dump()["refresh_token"] == "fresh"

    async def test_a_write_based_on_a_stale_read_is_refused(self):
        vault = FakeVault()
        existing = await _stored_account(vault, {"refresh_token": "r1"})
        repo = _repo(_FakeSession(existing=existing), vault)
        await repo.replace_credentials(
            existing.id, {"refresh_token": "r2"}, expected_version=1
        )

        with pytest.raises(SecretVersionConflict):
            await repo.replace_credentials(
                existing.id, {"refresh_token": "r2-too"}, expected_version=1
            )

        assert vault.value_of(existing.credentials_secret_id) == {"refresh_token": "r2"}

    async def test_replacing_credentials_on_an_account_with_none_creates_them(self):
        vault = FakeVault()
        existing = await _stored_account(vault, {"a": "b"})
        vault.secrets.clear()
        existing.credentials_secret_id = None
        session = _FakeSession(existing=existing)
        repo = _repo(session, vault)

        ref = await repo.replace_credentials(existing.id, {"bot_token": "t"})

        assert ref is not None
        assert vault.value_of(ref.id) == {"bot_token": "t"}
        assert vault.secrets[ref.id].scope == account_scope(
            existing.organization_id, existing.user_id
        )

    async def test_a_page_of_accounts_is_revealed_in_one_vault_read(self):
        vault = _CountingVault()
        rows = [
            await _stored_account(vault, {"access_token": str(i)}) for i in range(3)
        ]
        repo = _repo(_FakeSession(existing=rows), vault)

        accounts, _ = await repo.list_by_user(uuid4())

        assert vault.batch_reads == 1
        assert [a.credentials.model_dump()["access_token"] for a in accounts] == [
            "0",
            "1",
            "2",
        ]
        assert all(a.credentials_version == 1 for a in accounts)
