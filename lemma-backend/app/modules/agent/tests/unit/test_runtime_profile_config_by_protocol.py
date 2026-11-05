"""A profile's config is validated against its own protocol, never re-typed.

The config used to be an untagged union ending in a catch-all dict, and the
Anthropic shape has no required field. A harness config that failed its own
model -- a null timeout, say -- was read back as an Anthropic one: the revision
and the selections vanished from the entity, every dispatch failed, and the
next rename wrote the Anthropic shape back over the stored row for good.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.modules.agent.domain.errors import (
    RuntimeProfileUnreadableError,
    RuntimeProfileValidationError,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    AnthropicCompatibleRuntimeConfig,
    HarnessRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileProtocol,
    checked_profile,
)
from app.modules.agent.infrastructure.repositories.runtime_profile_repository import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentRuntimeProfileModel

pytestmark = pytest.mark.unit

_ORG = uuid4()
_SECRET_HEADER = "SECRET-header-value"

#: One stored row per protocol, in exactly the JSON shape the repository writes.
_STORED: dict[RuntimeProfileProtocol, dict[str, object]] = {
    RuntimeProfileProtocol.OPENAI_COMPATIBLE: {
        "base_url": "https://api.example.com/v1",
        "headers": {"X-Gateway": _SECRET_HEADER},
        "model_settings": {"temperature": 0.2},
    },
    RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE: {
        "base_url": None,
        "headers": {},
        "model_settings": {},
    },
    RuntimeProfileProtocol.AGENT_HOST: {
        "harness_snapshot_revision": "rev-7",
        "config_selections": {"mode": "plan"},
        "host_wait_timeout_seconds": 120,
    },
}

_TYPE: dict[RuntimeProfileProtocol, type[BaseModel]] = {
    RuntimeProfileProtocol.OPENAI_COMPATIBLE: OpenAICompatibleRuntimeConfig,
    RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE: AnthropicCompatibleRuntimeConfig,
    RuntimeProfileProtocol.AGENT_HOST: HarnessRuntimeConfig,
}


def _fields(protocol: RuntimeProfileProtocol, config: object) -> dict[str, object]:
    harness = protocol is RuntimeProfileProtocol.AGENT_HOST
    return {
        "id": str(uuid4()),
        "organization_id": _ORG,
        "user_id": uuid4() if harness else None,
        "harness_id": uuid4() if harness else None,
        "scope": "PERSONAL" if harness else "ORGANIZATION",
        "kind": "HARNESS" if harness else "MODEL_PROVIDER",
        "protocol": protocol.value,
        "name": "Profile",
        "default_model_name": None if harness else "model-a",
        "model_catalog": []
        if harness
        else [{"name": "model-a", "provider_model_name": "model-a"}],
        "config": config,
    }


class _Encryption:
    def encrypt_json(self, value: object) -> object:
        return value

    def decrypt_json(self, value: object) -> object:
        return value


@dataclass
class _Result:
    row: _StoredRow | None

    def scalar_one_or_none(self) -> _StoredRow | None:
        return self.row


@dataclass
class _Session:
    row: _StoredRow | None

    async def execute(self, _statement: object) -> _Result:
        return _Result(self.row)


@dataclass
class _UnitOfWork:
    session: _Session


def _repository(row: "_StoredRow | None") -> AgentRuntimeProfileRepository:
    return AgentRuntimeProfileRepository(
        _UnitOfWork(_Session(row)),  # type: ignore[arg-type]  # TST-03: one read is all this needs
        encryption=_Encryption(),  # type: ignore[arg-type]  # TST-03: the port's two methods
    )


@dataclass
class _StoredRow:
    """The columns of one stored profile, read by the model's own mapping.

    Not an ``AgentRuntimeProfileModel`` instance: building one configures every
    mapper, which needs the whole application's models imported, and what is
    under test is the mapping, not SQLAlchemy.
    """

    id: UUID
    organization_id: UUID
    user_id: object
    harness_id: object
    scope: object
    kind: object
    protocol: str
    name: str
    description: str | None
    default_model_name: object
    model_catalog: object
    config: dict[str, object]
    credentials: object
    status: str
    profile_metadata: dict[str, object]
    version: int

    to_entity = AgentRuntimeProfileModel.to_entity


def _row(protocol: RuntimeProfileProtocol, config: dict[str, object]) -> _StoredRow:
    fields = _fields(protocol, config)
    return _StoredRow(
        id=UUID(str(fields["id"])),
        organization_id=_ORG,
        user_id=fields["user_id"],
        harness_id=fields["harness_id"],
        scope=fields["scope"],
        kind=fields["kind"],
        protocol=protocol.value,
        name="Profile",
        description=None,
        default_model_name=fields["default_model_name"],
        model_catalog=fields["model_catalog"],
        config=config,
        credentials=None,
        status="ACTIVE",
        profile_metadata={},
        version=3,
    )


@pytest.mark.parametrize("protocol", list(RuntimeProfileProtocol))
def test_every_protocol_has_exactly_one_config_shape(
    protocol: RuntimeProfileProtocol,
) -> None:
    profile = AgentRuntimeProfile.model_validate(_fields(protocol, _STORED[protocol]))

    assert type(profile.config) is _TYPE[protocol]


@pytest.mark.parametrize("protocol", list(RuntimeProfileProtocol))
@pytest.mark.parametrize("foreign", list(RuntimeProfileProtocol))
def test_a_protocol_refuses_another_protocols_config(
    protocol: RuntimeProfileProtocol, foreign: RuntimeProfileProtocol
) -> None:
    if protocol is foreign:
        pytest.skip("its own shape")
    other = _TYPE[foreign].model_validate(_STORED[foreign])

    with pytest.raises(RuntimeProfileValidationError):
        checked_profile(**_fields(protocol, other))


def test_a_broken_harness_config_is_refused_not_read_as_another_shape() -> None:
    broken = {
        **_STORED[RuntimeProfileProtocol.AGENT_HOST],
        "host_wait_timeout_seconds": None,
    }

    with pytest.raises(ValidationError):
        AgentRuntimeProfile.model_validate(
            _fields(RuntimeProfileProtocol.AGENT_HOST, broken)
        )
    with pytest.raises(
        RuntimeProfileValidationError, match="host_wait_timeout_seconds"
    ):
        checked_profile(**_fields(RuntimeProfileProtocol.AGENT_HOST, broken))


def test_an_edit_cannot_slip_an_unvalidated_copy_past_the_shape() -> None:
    """``model_copy(update=...)`` runs no validator; the edit must catch it."""
    profile = AgentRuntimeProfile.model_validate(
        _fields(
            RuntimeProfileProtocol.AGENT_HOST,
            _STORED[RuntimeProfileProtocol.AGENT_HOST],
        )
    )
    assert isinstance(profile.config, HarnessRuntimeConfig)
    unchecked = profile.config.model_copy(update={"host_wait_timeout_seconds": None})

    with pytest.raises(RuntimeProfileValidationError):
        profile.with_changes(config=unchecked)


def test_a_refusal_names_the_field_and_quotes_none_of_the_input() -> None:
    bad = {"base_url": "not a url", "headers": {"X-Gateway": _SECRET_HEADER}}

    with pytest.raises(RuntimeProfileValidationError) as refused:
        checked_profile(**_fields(RuntimeProfileProtocol.OPENAI_COMPATIBLE, bad))

    message = str(refused.value)
    assert "config.base_url" in message
    assert _SECRET_HEADER not in message
    assert "input_value" not in message
    assert "validation error for" not in message
    assert refused.value.status_code == 400


@pytest.mark.parametrize("protocol", list(RuntimeProfileProtocol))
def test_a_stored_row_loads_and_writes_back_the_same_json(
    protocol: RuntimeProfileProtocol,
) -> None:
    repository = _repository(None)
    row = _row(protocol, dict(_STORED[protocol]))

    profile = repository._to_entity(row)  # type: ignore[arg-type]  # TST-03: the columns a read maps
    written = repository._apply_entity(_row(protocol, {}), profile)  # type: ignore[arg-type]  # TST-03: the columns an edit writes

    assert type(profile.config) is _TYPE[protocol]
    assert profile.version == 3
    assert written.config == _STORED[protocol]


@pytest.mark.asyncio
async def test_one_named_unreadable_row_is_a_typed_refusal() -> None:
    broken = {"config_selections": {}}  # a harness row with no revision
    repository = _repository(_row(RuntimeProfileProtocol.AGENT_HOST, broken))

    with pytest.raises(RuntimeProfileUnreadableError):
        await repository.get_visible_by_id(
            profile_id=str(uuid4()), organization_id=_ORG, user_id=uuid4()
        )


def test_a_capability_this_build_does_not_know_is_dropped_not_fatal() -> None:
    entry = RuntimeModelCatalogEntry.model_validate(
        {
            "name": "m",
            "provider_model_name": "m",
            "capabilities": ["TEXT", "AUDIO", "VISION"],
        }
    )

    assert entry.capabilities == [
        RuntimeModelCapability.TEXT,
        RuntimeModelCapability.VISION,
    ]
