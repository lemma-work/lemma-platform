"""A runtime profile's credentials and header values live in the vault.

Drives the real repository over an in-memory session and ``FakeVault``: what
reaches the row, what reaches the vault, and that callers get back the entity
they saved.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4, uuid7

import pytest

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    reveal_credentials,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.repositories.runtime_profile_repository import (
    SECRETS_PURPOSE,
)
from app.modules.agent.infrastructure.runtime_models import AgentRuntimeProfileModel
from app.modules.test_support.mappers import configure_test_mappers
from app.modules.test_support.vault_fake import FakeVault
from app.modules.vault.contracts import Revealed, SecretNotFound, SecretScope

# Building a model row configures the mapper graph, which resolves relationship
# targets by name. See `configure_test_mappers`.
configure_test_mappers()


class _Result:
    def __init__(self, rows: list[AgentRuntimeProfileModel]) -> None:
        self._rows = rows

    def scalars(self):
        return iter(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Holds added rows; every query returns all of them."""

    def __init__(self) -> None:
        self.rows: list[AgentRuntimeProfileModel] = []

    def add(self, row: AgentRuntimeProfileModel) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        for row in self.rows:
            if row.id is None:
                row.id = uuid7()

    async def execute(self, _stmt) -> _Result:
        return _Result(self.rows)


class _UnitOfWork:
    def __init__(self) -> None:
        self.session = _Session()


class _CountingVault(FakeVault):
    def __init__(self) -> None:
        super().__init__()
        self.single_reveals = 0
        self.batch_reveals = 0

    async def reveal(self, secret_id, **kwargs) -> Revealed:
        self.single_reveals += 1
        return await super().reveal(secret_id, **kwargs)

    async def reveal_many(
        self, expected: Mapping[UUID, SecretScope], *, purpose: str
    ) -> dict[UUID, Revealed]:
        self.batch_reveals += 1
        return await super().reveal_many(expected, purpose=purpose)


def _repository(vault: FakeVault) -> tuple[AgentRuntimeProfileRepository, _Session]:
    uow = _UnitOfWork()
    repository = AgentRuntimeProfileRepository(
        uow,  # type: ignore[arg-type]
        vault=vault,
    )
    return repository, uow.session


def _headers(profile: AgentRuntimeProfile) -> object:
    return profile.model_dump(mode="json")["config"].get("headers")


def _profile(
    *,
    organization_id: UUID,
    name: str = "Vendor",
    api_key: str | None = "sk-real",
    headers: dict[str, str] | None = None,
    user_id: UUID | None = None,
) -> AgentRuntimeProfile:
    personal = user_id is not None
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        user_id=user_id,
        scope=RuntimeProfileScope.PERSONAL
        if personal
        else RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name=name,
        default_model_name="model-a",
        model_catalog=[
            RuntimeModelCatalogEntry(name="model-a", provider_model_name="model-a")
        ],
        config=OpenAICompatibleRuntimeConfig(
            base_url="https://api.vendor.test/v1", headers=headers or {}
        ),
        credentials=ApiKeyRuntimeCredentials(api_key=api_key) if api_key else None,
    )


async def test_header_values_leave_the_row_and_come_back_on_read() -> None:
    vault = FakeVault()
    repository, session = _repository(vault)
    organization_id, user_id = uuid4(), uuid4()

    created = await repository.create(
        _profile(
            organization_id=organization_id,
            user_id=user_id,
            headers={"Authorization": "Bearer header-secret"},
        )
    )

    (row,) = session.rows
    assert "headers" not in row.config
    assert "header-secret" not in str(row.config)
    assert row.secrets_secret_id is not None
    stored = vault.secrets[row.secrets_secret_id]
    assert stored.value == {
        "credentials": {"api_key": "sk-real"},
        "headers": {"Authorization": "Bearer header-secret"},
    }
    assert stored.purpose == SECRETS_PURPOSE
    # Organization only: user_id is SET NULL on user deletion.
    assert stored.scope == SecretScope(organization_id=organization_id)
    assert stored.owner_table == "agent_runtime_profiles"

    assert _headers(created) == {"Authorization": "Bearer header-secret"}
    assert reveal_credentials(created.credentials) == {"api_key": "sk-real"}


async def test_an_edit_replaces_the_same_secret_and_skips_an_unchanged_one() -> None:
    vault = FakeVault()
    repository, session = _repository(vault)
    created = await repository.create(_profile(organization_id=uuid4()))
    (row,) = session.rows
    secret_id = row.secrets_secret_id
    assert secret_id is not None

    await repository.update(created.model_copy(update={"description": "renamed"}))
    assert vault.secrets[secret_id].version == 1

    rotated = created.model_copy(
        update={"credentials": ApiKeyRuntimeCredentials(api_key="sk-rotated")}
    )
    updated = await repository.update(rotated)

    assert row.secrets_secret_id == secret_id
    assert vault.secrets[secret_id].version == 2
    assert reveal_credentials(updated.credentials) == {"api_key": "sk-rotated"}


async def test_a_profile_with_nothing_secret_points_at_no_secret() -> None:
    vault = FakeVault()
    repository, session = _repository(vault)
    created = await repository.create(
        _profile(organization_id=uuid4(), headers={"X-Tenant": "t"})
    )
    (row,) = session.rows
    assert row.secrets_secret_id is not None

    cleared = created.model_copy(
        update={
            "credentials": None,
            "config": OpenAICompatibleRuntimeConfig(
                base_url="https://api.vendor.test/v1"
            ),
        }
    )
    reread = await repository.update(cleared)

    # The vault_owned trigger deletes the secret the column stopped pointing at.
    assert row.secrets_secret_id is None
    assert reread.credentials is None
    assert not _headers(reread)


async def test_a_listing_reveals_every_profile_in_one_vault_call() -> None:
    vault = _CountingVault()
    repository, _session = _repository(vault)
    organization_id = uuid4()
    for index in range(3):
        await repository.create(
            _profile(
                organization_id=organization_id,
                name=f"Vendor {index}",
                api_key=f"sk-{index}",
            )
        )
    await repository.create(
        _profile(organization_id=organization_id, name="Keyless", api_key=None)
    )
    vault.single_reveals = vault.batch_reveals = 0

    listed = await repository.get_visible(
        organization_id=organization_id, user_id=uuid4()
    )

    assert (vault.single_reveals, vault.batch_reveals) == (0, 1)
    assert {
        profile.name: reveal_credentials(profile.credentials) for profile in listed
    } == {
        "Vendor 0": {"api_key": "sk-0"},
        "Vendor 1": {"api_key": "sk-1"},
        "Vendor 2": {"api_key": "sk-2"},
        "Keyless": None,
    }


async def test_a_secret_bound_to_another_organization_does_not_read() -> None:
    """The expected scope comes from the row, so a repointed id is refused."""
    vault = FakeVault()
    repository, session = _repository(vault)
    created = await repository.create(_profile(organization_id=uuid4()))
    (row,) = session.rows
    row.organization_id = uuid4()

    with pytest.raises(SecretNotFound):
        await repository.get_visible_by_id(
            profile_id=created.id,
            organization_id=row.organization_id,
            user_id=uuid4(),
        )
