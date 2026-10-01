from __future__ import annotations

import pytest

from app.modules.agent.services.pod_mcp_service import POD_MCP_TOOLSETS
from app.modules.agent.services.pod_mcp_tool_policy import (
    POD_TOOL_POLICIES,
    policy_for,
    scope_for_call,
    without_approval_envelope,
)
from app.modules.agent.tools.decisions.pydantic_adapter import decisions_toolset
from app.modules.mcp_access.contracts import Scope

pytestmark = pytest.mark.unit


def test_every_served_tool_has_a_policy_row():
    """A tool added without a row would be offered to outside clients under the
    cautious default -- a write needing `pod:write` -- which is safe but wrong
    for a read. Make the author decide."""
    served = {name for toolset in POD_MCP_TOOLSETS for name in toolset.tools}
    assert served == set(POD_TOOL_POLICIES)


def test_the_decision_tools_are_served_and_asking_is_reading():
    """Asking and trying a decider record a log entry at most, not pod data;
    saving a decider and answering a decision change what the pod holds."""
    assert decisions_toolset in POD_MCP_TOOLSETS
    for name in ("decide", "test_decider"):
        assert policy_for(name).scope is Scope.READ
        assert policy_for(name).annotations().read_only_hint is True
    for name in ("define_decider", "answer_decision"):
        assert policy_for(name).scope is Scope.WRITE
        assert policy_for(name).annotations().read_only_hint is False


def test_writing_tools_need_the_write_scope_and_say_they_change_things():
    for name in ("pod_write_record", "pod_write_file", "pod_edit_file"):
        policy = policy_for(name)
        assert policy.scope is Scope.WRITE
        annotations = policy.annotations()
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is True


def test_reading_tools_are_annotated_read_only():
    annotations = policy_for("pod_query").annotations()
    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.open_world_hint is False


@pytest.mark.parametrize(
    "arguments",
    [
        {"request": {"path": "/a.pdf", "url_type": "public"}},
        {"path": "/a.pdf", "url_type": "public"},
    ],
)
def test_a_public_file_link_is_publishing_and_needs_write(arguments):
    """It outlives the connection and anyone can open it for up to a week."""
    assert scope_for_call("pod_get_file_url", arguments) is Scope.WRITE
    assert policy_for("pod_get_file_url").annotations().open_world_hint is True


@pytest.mark.parametrize(
    "arguments", [None, {}, {"request": {"path": "/a.pdf"}}, {"url_type": "app"}]
)
def test_an_in_app_file_link_is_reading(arguments):
    assert scope_for_call("pod_get_file_url", arguments) is Scope.READ


def test_an_unknown_tool_gets_the_most_cautious_policy():
    assert policy_for("pod_something_new").scope is Scope.WRITE


def test_the_approval_hand_off_is_removed_for_outside_clients():
    denied = {
        "success": False,
        "error": "Not allowed",
        "code": "FORBIDDEN",
        "needs_approval": True,
        "approval": {"tool_name": "pod_write_record"},
    }
    assert without_approval_envelope(denied) == {
        "success": False,
        "error": "Not allowed",
        "code": "FORBIDDEN",
    }
    assert without_approval_envelope({"success": True}) == {"success": True}
    assert without_approval_envelope("text") == "text"


def test_deciding_rows_needs_write_because_the_results_become_a_file() -> None:
    assert scope_for_call("decide", {"state": {"subject": "hi"}}) is Scope.READ
    assert scope_for_call("decide", {"file": "/me/tickets.csv"}) is Scope.WRITE
    assert scope_for_call("decide", {"request": {"items": [{"a": 1}]}}) is Scope.WRITE
