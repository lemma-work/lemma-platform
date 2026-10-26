"""Every tool's replay class is on its policy, and the classes say what §14 says.

A turn resumed after a crash settles the call its last attempt left open by
this class: SAFE and KEYED run again under the recorded call id, UNSAFE never
does, and neither does a call no spec answers to.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES
from app.modules.agent.domain.replay import (
    ReplayClass,
    is_replayable_call,
    replay_class,
)
from app.modules.agent.domain.tools import ToolSpec
from app.modules.agent.infrastructure.tools.contact import contact_specs
from app.modules.agent.infrastructure.tools.final_answer.tools import (
    final_answer_spec,
)
from app.modules.agent.infrastructure.tools.form import form_specs
from app.modules.agent.infrastructure.tools.messaging import answer
from app.modules.agent.infrastructure.tools.todo import todo_specs
from app.modules.agent.services.tool_catalog import STATIC_SPECS

pytestmark = pytest.mark.unit

SAFE, KEYED, UNSAFE = ReplayClass.SAFE, ReplayClass.KEYED, ReplayClass.UNSAFE


def _specs() -> dict[str, ToolSpec]:
    specs = [spec for group in STATIC_SPECS.values() for spec in group]
    specs += [
        *answer.SPECS,
        *contact_specs(uow_factory=object),
        *form_specs(uow_factory=object),
        *todo_specs(uow_factory=object),
        final_answer_spec(agent=None),
    ]
    return {spec.name: spec for spec in specs}


def _class(tool: str | None, args: object = None) -> ReplayClass:
    return replay_class(_specs().get(tool or ""), args)


def test_pauses_are_never_settled_by_a_resumed_turn() -> None:
    specs = _specs()
    for name in PAUSING_TOOL_NAMES:
        assert specs[name].policy.pauses is not None
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
    assert _class(tool) is expected


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
    assert _class("pod_write_record", args) is expected


def test_awaiting_a_child_is_safe_and_messaging_it_is_not() -> None:
    assert _class("interact_subagent", {"action": "await"}) is SAFE
    assert _class("interact_subagent", {"request": {"action": "stop"}}) is SAFE
    assert _class("interact_subagent", {"action": "send"}) is UNSAFE
