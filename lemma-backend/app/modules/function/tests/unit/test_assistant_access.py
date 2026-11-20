"""A function kept from the assistant: refused to it, and to it alone.

The case behind it: a plugin's app called its "claim" function after checking
a daily limit, and the assistant -- shown every function, able to run any by
name -- called "claim" directly, past the limit.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization.actor import Delegation
from app.core.authorization.app_sessions import app_session_id, is_the_assistant
from app.core.authorization.context import (
    Actor,
    Assistant,
    Context,
    Person,
    Workload,
    WorkloadKind,
)
from app.modules.function.domain.entities import FunctionEntity
from app.modules.function.domain.errors import FunctionNotForAssistantError
from app.modules.function.services.assistant_access import require_runnable_by

pytestmark = pytest.mark.unit

POD_ID = uuid4()
USER_ID = uuid4()


def _ctx(actor: Actor) -> Context:
    return Context(
        actor=actor,
        actor_id="actor",
        user_id=USER_ID,
        pod_id=POD_ID,
        authorizer=object(),
    )


def _function(*, assistant_invoke: bool) -> FunctionEntity:
    return FunctionEntity(
        pod_id=POD_ID,
        user_id=USER_ID,
        name="claim",
        assistant_invoke=assistant_invoke,
    )


#: The assistant in its own conversation: the CLI in its sandbox, an Agent
#: Host's tools, and an AI tool running a function as a tool (no session).
_THE_ASSISTANT = [
    Assistant(user_id=USER_ID, delegation=Delegation(session_id=str(uuid4()))),
    Assistant(user_id=USER_ID),
]

#: Everyone else who may run it as part of a flow.
_A_FLOW = [
    Person(user_id=USER_ID),
    Assistant(
        user_id=USER_ID,
        delegation=Delegation(session_id=app_session_id(uuid4())),
    ),
    Workload(kind=WorkloadKind.FUNCTION, workload_id=uuid4(), for_user_id=USER_ID),
    Workload(kind=WorkloadKind.AGENT, workload_id=uuid4(), for_user_id=USER_ID),
]


@pytest.mark.parametrize("actor", _THE_ASSISTANT, ids=["conversation", "mcp-tool"])
def test_the_assistant_is_refused_a_function_kept_from_it(actor: Actor):
    with pytest.raises(FunctionNotForAssistantError) as refused:
        require_runnable_by(_function(assistant_invoke=False), _ctx(actor))

    assert refused.value.status_code == 403
    assert "claim" in str(refused.value)
    # Product copy: the noun is "a Lemma", never "pod".
    assert "pod" not in str(refused.value).lower()


@pytest.mark.parametrize(
    "actor", _A_FLOW, ids=["person", "framed-app", "function", "named-agent"]
)
def test_a_flow_still_runs_it(actor: Actor):
    require_runnable_by(_function(assistant_invoke=False), _ctx(actor))
    assert not is_the_assistant(_ctx(actor))


@pytest.mark.parametrize("actor", _THE_ASSISTANT + _A_FLOW)
def test_a_function_offered_to_the_assistant_runs_for_anyone(actor: Actor):
    require_runnable_by(_function(assistant_invoke=True), _ctx(actor))


def test_a_function_is_offered_to_the_assistant_unless_kept_from_it():
    assert FunctionEntity(pod_id=POD_ID, user_id=USER_ID, name="x").assistant_invoke
