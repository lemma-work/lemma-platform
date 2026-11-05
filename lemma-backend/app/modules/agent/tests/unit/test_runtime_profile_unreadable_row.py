"""An unreadable profile row is skipped without its decrypted key in the log.

The listing skips a row it cannot validate rather than failing the whole page,
and says so. It used to decrypt the key into the row before validating it and
log the validation error's text, which quotes the input it rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.modules.agent.infrastructure.repositories.runtime_profile_repository import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentRuntimeProfileModel

pytestmark = pytest.mark.unit

_DECRYPTED = "sk-decrypted-must-not-be-logged"


class _Encryption:
    def encrypt_json(self, value: object) -> object:
        return value

    def decrypt_json(self, value: object) -> object:
        # A key stored in a shape this build no longer accepts.
        return _DECRYPTED


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
        credentials={"ciphertext": "opaque"},
        status="ACTIVE",
        profile_metadata={},
    )
    repository = AgentRuntimeProfileRepository(
        _UnitOfWork(_Session([row])),  # type: ignore[arg-type]  # TST-03: a two-method stand-in for the session
        encryption=_Encryption(),  # type: ignore[arg-type]  # TST-03: the port's two methods
    )

    profiles = await repository.get_visible(
        organization_id=organization_id, user_id=uuid4()
    )

    assert profiles == []
    logged = caplog.text + capsys.readouterr().out
    assert "agent.runtime_profile.unreadable.skipped" in logged
    assert _DECRYPTED not in logged
