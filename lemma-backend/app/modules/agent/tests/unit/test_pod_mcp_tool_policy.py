"""How each pod tool presents itself to an outside MCP client, from its policy."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.mcp.toolspec_adapter import mcp_tools
from app.modules.agent.infrastructure.tools.pod.tools import SPECS
from app.modules.agent.services.pod_mcp_service import (
    POD_TOOLS,
    scope_for_call,
    without_approval_envelope,
)
from app.modules.mcp_access.contracts import Scope

pytestmark = pytest.mark.unit


async def _listed() -> dict:
    ctx = ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())
    tools = await mcp_tools(POD_TOOLS, ctx, view_meta=lambda _view: {})
    return {tool.name.removeprefix("lemma_"): tool for tool in tools}


def test_every_pod_tool_is_titled_for_an_outside_client():
    """A tool without a title shows its raw name in a client's confirmation."""
    assert all(spec.policy.title for spec in SPECS)


@pytest.mark.anyio
async def test_writing_tools_need_the_write_scope_and_say_they_change_things():
    listed = await _listed()
    for name in ("pod_write_record", "pod_write_file", "pod_edit_file"):
        assert scope_for_call(name, None) is Scope.WRITE
        annotations = listed[name].annotations
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is True


@pytest.mark.anyio
async def test_reading_tools_are_annotated_read_only():
    annotations = (await _listed())["pod_query"].annotations
    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.open_world_hint is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"request": {"path": "/a.pdf", "url_type": "public"}},
        {"path": "/a.pdf", "url_type": "public"},
    ],
)
async def test_a_public_file_link_is_publishing_and_needs_write(arguments):
    """It outlives the connection and anyone can open it for up to a week."""
    assert scope_for_call("pod_get_file_url", arguments) is Scope.WRITE
    assert (await _listed())["pod_get_file_url"].annotations.open_world_hint is True


@pytest.mark.parametrize(
    "arguments", [None, {}, {"request": {"path": "/a.pdf"}}, {"url_type": "app"}]
)
def test_an_in_app_file_link_is_reading(arguments):
    assert scope_for_call("pod_get_file_url", arguments) is Scope.READ


def test_an_unknown_tool_needs_the_widest_scope():
    assert scope_for_call("pod_something_new", None) is Scope.WRITE


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
