"""An unreadable profile row is skipped without its decrypted key in the log.

The listing skips a row it cannot validate rather than failing the whole page,
and says so. It used to decrypt the key into the row before validating it and
log the validation error's text, which quotes the input it rejected. A header
value, revealed from the vault with the credentials, is as secret as the key.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.modules.agent.infrastructure.repositories.runtime_profile_repository import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentRuntimeProfileModel

pytestmark = pytest.mark.unit

_DECRYPTED = "sk-decrypted-must-not-be-logged"


@dataclass
class _Revealed:
    value: dict[str, object]

    def json(self) -> dict[str, object]:
        return self.value


class _Vault:
    async def reveal_many(
        self, expected: dict[UUID, object], *, purpose: str
    ) -> dict[UUID, _Revealed]:
        # A header value stored in a shape this build no longer accepts.
        return {
            secret_id: _Revealed(
                {"credentials": None, "headers": {"Authorization": [_DECRYPTED]}}
            )
            for secret_id in expected
        }


@dataclass
class _Scalars:
    rows: list[AgentRuntimeProfileModel]

    def scalars(self) -> list[AgentRuntimeProfileModel]:
        return self.rows


@dataclass
class _Session:
    rows: list[AgentRuntimeProfileModel]

    async def execute(self, _statement: object) -> _Scalars:
        return _Scalars(self.rows)


@dataclass
class _UnitOfWork:
    session: _Session


@pytest.mark.asyncio
async def test_an_unreadable_row_is_skipped_without_logging_its_key(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    organization_id = uuid4()
    row = AgentRuntimeProfileModel(
        id=uuid4(),
        organization_id=organization_id,
        scope="ORGANIZATION",
        kind="MODEL_PROVIDER",
        runtime_type="MODEL_PROVIDER",
        protocol="OPENAI_COMPATIBLE",
        name="Vendor",
        default_model_name="model-a",
        model_catalog=[{"name": "model-a", "provider_model_name": "model-a"}],
        config={"base_url": "https://api.example.com/v1"},
        credentials=None,
        secrets_secret_id=uuid4(),
        status="ACTIVE",
        profile_metadata={},
    )
    repository = AgentRuntimeProfileRepository(
        _UnitOfWork(_Session([row])),  # type: ignore[arg-type]  # TST-03: a two-method stand-in for the session
        vault=_Vault(),  # type: ignore[arg-type]  # TST-03: the one vault call a listing makes
    )

    profiles = await repository.get_visible(
        organization_id=organization_id, user_id=uuid4()
    )

    assert profiles == []
    logged = caplog.text + capsys.readouterr().out
    assert "agent.runtime_profile.unreadable.skipped" in logged
    assert _DECRYPTED not in logged
