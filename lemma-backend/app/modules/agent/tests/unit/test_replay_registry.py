"""Every tool a turn can call has a declared replay class, and the classes say what §14 says.

A turn resumed after a crash settles the call its last attempt left open by
this class: SAFE and KEYED run again under the recorded call id, UNSAFE never
does. A tool added without a row would silently default to UNSAFE -- safe, but
an author's decision nobody made -- so the registry must cover every tool the
static toolsets offer.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.domain.replay import (
    REPLAY_CLASS,
    ReplayClass,
    is_replayable_call,
    replay_class,
)
from app.modules.agent.infrastructure.tools.registry import _TOOLSET_BY_NAME

pytestmark = pytest.mark.unit

SAFE, KEYED, UNSAFE = ReplayClass.SAFE, ReplayClass.KEYED, ReplayClass.UNSAFE


def _static_tool_names() -> set[str]:
    names: set[str] = set()
    for toolset in _TOOLSET_BY_NAME.values():
        names.update(getattr(toolset, "tools", {}))
    return names


def test_every_static_tool_is_classified_or_is_a_pause() -> None:
    missing = _static_tool_names() - set(REPLAY_CLASS) - set(PAUSING_TOOL_NAMES)
    assert not missing, f"tools with no replay class: {sorted(missing)}"


def test_pauses_are_never_settled_by_a_resumed_turn() -> None:
    assert not set(PAUSING_TOOL_NAMES) & set(REPLAY_CLASS)
    for name in PAUSING_TOOL_NAMES:
        assert not is_replayable_call(name)
    assert is_replayable_call("pod_read_file")


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("pod_read_file", SAFE),
        ("pod_query", SAFE),
        ("web_fetch", SAFE),
        ("view_image", SAFE),
        # Writes the same bytes twice, but over whatever was written between.
        ("pod_write_file", UNSAFE),
        ("channel_post", KEYED),
        ("channel_read", SAFE),
        ("answer_question", KEYED),
        ("contact_function", KEYED),
        ("spawn_subagent", KEYED),
        ("exec_command", UNSAFE),
        ("execute_python", UNSAFE),
        ("pod_edit_file", UNSAFE),
        ("run_connector_operation", UNSAFE),
        ("some_mcp_server_tool", UNSAFE),
        (None, UNSAFE),
    ],
)
def test_the_defaults_follow_the_design(
    tool: str | None, expected: ReplayClass
) -> None:
    assert replay_class(tool) is expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"request": {"action": "create", "table_name": "t"}}, KEYED),
        # Neither records the row's state when the step began, so a replay
        # could put back a value someone changed since: outcome unknown.
        ({"action": "update", "record_id": "1"}, UNSAFE),
        ({"request": {"action": "delete"}}, UNSAFE),
        ({"request": {"action": "UPDATE"}}, UNSAFE),
        ({}, UNSAFE),
        ("not a mapping", UNSAFE),
    ],
)
def test_a_record_write_is_classified_by_its_operation(args, expected) -> None:
    assert replay_class("pod_write_record", args) is expected


def test_awaiting_a_child_is_safe_and_messaging_it_is_not() -> None:
    assert replay_class("interact_subagent", {"action": "await"}) is SAFE
    assert replay_class("interact_subagent", {"request": {"action": "stop"}}) is SAFE
    assert replay_class("interact_subagent", {"action": "send"}) is UNSAFE
