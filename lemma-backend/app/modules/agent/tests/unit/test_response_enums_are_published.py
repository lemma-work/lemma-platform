"""Fields with a closed set of values say so in the published schema.

``visibility`` and a profile's ``availability_status`` were bare strings, so a
generated client could not tell its users what values to expect -- and nothing
stopped a typo from being served.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.authorization.context import ResourceVisibility
from app.modules.agent.api.runtime_profile_schemas import AgentRuntimeProfileResponse
from app.modules.agent.api.schemas import AgentResponse, AgentSummaryResponse
from app.modules.agent.domain.runtime_profiles import RuntimeProfileAvailability

pytestmark = pytest.mark.unit


def _enum_of(schema: dict[str, object], field: str) -> list[object]:
    """The enum a field's JSON schema allows, following ``$ref`` and ``anyOf``."""
    definitions = schema.get("$defs", {})
    node = schema["properties"][field]  # type: ignore[index]  # TST-03: a JSON schema dict
    for candidate in [node, *node.get("anyOf", [])]:
        ref = candidate.get("$ref")
        if ref:
            candidate = definitions[ref.rsplit("/", 1)[-1]]
        if "enum" in candidate:
            return list(candidate["enum"])
    return []


@pytest.mark.parametrize("model", [AgentResponse, AgentSummaryResponse])
def test_visibility_is_published_as_its_enum(model: type) -> None:
    assert _enum_of(model.model_json_schema(), "visibility") == [
        member.value for member in ResourceVisibility
    ]


def test_availability_is_published_as_its_enum() -> None:
    assert _enum_of(
        AgentRuntimeProfileResponse.model_json_schema(), "availability_status"
    ) == [member.value for member in RuntimeProfileAvailability]


def test_a_legacy_visibility_spelling_still_reads() -> None:
    now = datetime.now(timezone.utc)
    agent = AgentResponse(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="helper",
        instruction="help",
        visibility="private",
        created_at=now,
        updated_at=now,
    )

    assert agent.visibility is ResourceVisibility.PERSONAL
