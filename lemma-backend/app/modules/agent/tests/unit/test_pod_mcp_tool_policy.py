from __future__ import annotations

import pytest

from app.modules.agent.services.pod_mcp_tool_policy import (
    POD_TOOL_POLICIES,
    policy_for,
    without_approval_envelope,
)
from app.modules.agent.tools.pod.pydantic_adapter import pod_toolset
from app.modules.mcp_access.contracts import Scope

pytestmark = pytest.mark.unit


def test_every_pod_tool_has_a_policy_row():
    """A tool added without a row would be offered to outside clients under the
    cautious default -- a write needing `pod:write` -- which is safe but wrong
    for a read. Make the author decide."""
    assert set(pod_toolset.tools) == set(POD_TOOL_POLICIES)


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
