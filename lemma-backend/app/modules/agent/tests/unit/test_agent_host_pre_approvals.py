"""Which late approval a resume run carries to its Agent Host, and how the
host's answer from it is recorded.

The round trip -- a turn ended past its parked window, the late approval, the
resume run's spec and the host answering from it -- is driven against Postgres
in ``test_agent_host_parked_window_e2e``. What is decided from messages alone
is here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.modules.agent.domain.agent_host import AgentHostRunSpec
from app.modules.agent.domain.agent_host_permissions import (
    permission_approval_tool_args,
    pre_approved_by,
    pre_approved_events,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentEventType,
    JsonObject,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.agent_host.pre_approvals import (
    pre_approvals_for,
    supports_pre_approval,
)

_APPROVAL = "agent-host-permission:native-shell"
_PAYLOAD: JsonObject = {
    "tool": {"name": "exec_command", "source": "native"},
    "input": {"cmd": "rm -rf build"},
    "options": [{"option_id": "once", "name": "Allow", "kind": "allow_once"}],
}


def _message(sequence: int, **fields: object) -> Message:
    return Message(
        id=uuid4(),
        conversation_id=uuid4(),
        sequence=sequence,
        created_at=datetime.now(timezone.utc),
        **fields,
    )


def _asked(
    tool_call_id: str = _APPROVAL, tool_args: JsonObject | None = None
) -> Message:
    return _message(
        1,
        role=MessageRole.ASSISTANT,
        kind=MessageKind.TOOL_CALL,
        tool_name="request_approval",
        tool_call_id=tool_call_id,
        tool_args=(
            tool_args
            if tool_args is not None
            else permission_approval_tool_args(
                _PAYLOAD, request_id="native-shell", tool_name="exec_command"
            )
        ),
    )


def _answered(decision: str, tool_call_id: str = _APPROVAL) -> Message:
    return _message(
        2,
        role=MessageRole.TOOL,
        kind=MessageKind.TOOL_RETURN,
        tool_name="request_approval",
        tool_call_id=tool_call_id,
        tool_result={"success": True, "message": "…", "decision": decision},
    )


def test_an_approved_permission_is_carried_with_its_exact_call() -> None:
    (carried,) = pre_approvals_for(
        [_asked(), _answered("APPROVE_FOR_SESSION")], resumed_tool_call_id=_APPROVAL
    )

    assert carried.model_dump(mode="json") == {
        "approval_id": _APPROVAL,
        "tool_name": "exec_command",
        "input": {"cmd": "rm -rf build"},
        "decision": "APPROVE_FOR_SESSION",
    }


def test_a_denial_is_never_carried() -> None:
    assert (
        pre_approvals_for([_asked(), _answered("DENY")], resumed_tool_call_id=_APPROVAL)
        == []
    )


def test_nothing_is_carried_without_a_decided_host_permission() -> None:
    approved = [_asked(), _answered("APPROVE_ONCE")]
    # Not a resume run, or a resume of something else.
    assert pre_approvals_for(approved, resumed_tool_call_id=None) == []
    assert pre_approvals_for(approved, resumed_tool_call_id="lemma-mcp-1") == []
    # Not answered yet.
    assert pre_approvals_for([_asked()], resumed_tool_call_id=_APPROVAL) == []
    # An ordinary approval under the same prefix, without the host's marker.
    unmarked = [
        _asked(tool_args={"title": "Delete", "tool_name": "exec_command"}),
        _answered("APPROVE_ONCE"),
    ]
    assert pre_approvals_for(unmarked, resumed_tool_call_id=_APPROVAL) == []
    # A decision this build does not know.
    assert (
        pre_approvals_for(
            [_asked(), _answered("SOMETHING_ELSE")], resumed_tool_call_id=_APPROVAL
        )
        == []
    )


def test_only_a_harness_that_publishes_it_honours_pre_approval() -> None:
    assert supports_pre_approval({"load_session": True, "pre_approval": True})
    assert not supports_pre_approval({"load_session": True})
    assert not supports_pre_approval({})


def test_the_run_spec_carries_pre_approvals_and_defaults_to_none() -> None:
    spec = {
        "agent_run_id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "harness_id": str(uuid4()),
        "profile_revision": "rev-1",
        "system_prompt": "",
        "prompt": [{"type": "text", "text": "hi"}],
        "run_deadline": "2026-10-13T00:00:00Z",
    }
    assert AgentHostRunSpec.model_validate(spec).pre_approvals == []
    (carried,) = pre_approvals_for(
        [_asked(), _answered("APPROVE_ONCE")], resumed_tool_call_id=_APPROVAL
    )
    wire = AgentHostRunSpec.model_validate(
        {**spec, "pre_approvals": [carried.model_dump(mode="json")]}
    ).model_dump(mode="json")
    assert wire["pre_approvals"][0]["tool_name"] == "exec_command"


def test_a_request_the_host_answered_is_recorded_resolved_with_no_buttons() -> None:
    payload = {**_PAYLOAD, "pre_approved": {"approval_id": _APPROVAL}}
    assert pre_approved_by(payload) == _APPROVAL
    assert pre_approved_by(_PAYLOAD) is None

    events = pre_approved_events(
        agent_run_id=uuid4(),
        request_id="native-shell-2",
        approval_id=_APPROVAL,
        sequence=4,
        payload=payload,
        metadata={},
        tool_name="exec_command",
    )

    # The call and its answer, and no STATUS: that is what posts buttons on a
    # surface, and there is nothing left to decide.
    assert [event.type for event in events] == [AgentEventType.MESSAGE] * 2
    call, answer = (event.data for event in events)
    assert call.kind is MessageKind.TOOL_CALL
    assert call.tool_call_id == answer.tool_call_id
    assert call.tool_call_id == "agent-host-permission:native-shell-2"
    assert call.tool_args["agent_host_permission"]["pre_approved"] == {
        "approval_id": _APPROVAL
    }
    assert answer.kind is MessageKind.TOOL_RETURN
    assert answer.tool_result["response"] == {"approved_earlier": _APPROVAL}
