"""`takes_input` on the agent summary: the list endpoint's answer to
"can I talk to this one?".

`AgentSummaryResponse` deliberately omits `input_schema`, so a caller holding a
listed agent cannot tell a conversational agent from one that is called with
typed arguments. Anything that reads the omitted field instead of this boolean
silently sees `{}` for every agent and filters nothing — which is why this is a
server-side derivation and not a frontend predicate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agent.api.controllers.agent_controller import (
    _agent_summary_response,
)

pytestmark = pytest.mark.unit


def _agent(input_schema):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        pod_id="22222222-2222-2222-2222-222222222222",
        user_id="33333333-3333-3333-3333-333333333333",
        name="socratic-guide",
        description=None,
        icon_url=None,
        visibility="POD",
        toolsets=[],
        metadata=None,
        created_at="2026-08-20T00:00:00Z",
        updated_at="2026-08-20T00:00:00Z",
        allowed_actions=[],
        agent_runtime=None,
        input_schema=input_schema,
    )


@pytest.mark.parametrize(
    "input_schema",
    [
        None,
        {},
        # The agent builder writes this the moment someone opens the schema
        # editor and adds no fields. It declares nothing, so it takes nothing.
        {"type": "object", "properties": {}},
    ],
    ids=["absent", "empty", "properties-empty"],
)
def test_conversational_agent_takes_no_input(input_schema):
    assert _agent_summary_response(_agent(input_schema)).takes_input is False


def test_agent_with_declared_properties_takes_input():
    schema = {"type": "object", "properties": {"topic": {"type": "string"}}}

    assert _agent_summary_response(_agent(schema)).takes_input is True
