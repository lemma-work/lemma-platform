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


class _FakeSession:
    def __init__(self, *, flush_exc: Exception | None = None, existing=None):
        self._flush_exc = flush_exc
        self._existing = existing
        self.added = []

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        if self._flush_exc is not None:
            raise self._flush_exc

    async def refresh(self, instance, attribute_names=None):
        return None

    async def execute(self, stmt):
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
    defaults = dict(
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        connector_id="asana",
        status=AccountStatus.CONNECTED,
        provider_account_id="acc-1",
        credentials={"access_token": "tok"},
    )
    defaults.update(overrides)
    return AccountEntity(**defaults)


def _duplicate_identity_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO accounts ...",
        {},
        Exception(
            'duplicate key value violates unique constraint '
            '"uq_accounts_provider_identity"'
        ),
    )


def _unrelated_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO accounts ...",
        {},
        Exception(
            'duplicate key value violates unique constraint '
            '"uq_accounts_default_per_auth_config"'
        ),
    )


async def test_create_translates_duplicate_identity_violation():
    session = _FakeSession(flush_exc=_duplicate_identity_error())
    repo = AccountRepository(uow=_FakeUow(session), encryption=_NoopEncryption())

    with pytest.raises(AccountAlreadyConnectedError):
        await repo.create(_account_entity())


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
