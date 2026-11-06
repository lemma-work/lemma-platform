"""The toolset experiment's arms: which tools the pod assistant sees, by profile."""

from __future__ import annotations

import pytest

from app.modules.agent.domain.run_kind import RunKind
from app.modules.agent.domain.tool_profile import ToolProfile, run_tool_profile
from app.modules.agent.infrastructure.harness.lemma.capabilities import _arm, _groups
from app.modules.agent.services.tool_catalog import STATIC_SPECS

pytestmark = pytest.mark.unit


def _names(groups) -> set[str]:
    return {spec.name for group in groups for spec in group}


def _all_groups():
    return _groups(spec for specs in STATIC_SPECS.values() for spec in specs)


def test_today_defers_the_optional_tools_and_shows_delegate() -> None:
    shown, deferred = _arm(_all_groups(), ToolProfile.TODAY, defers=True, natively=True)
    assert "delegate" in _names(shown)
    assert "spawn_subagent" in _names(deferred)
    assert "exec_command" in _names(shown)


def test_direct_shows_everything() -> None:
    shown, deferred = _arm(
        _all_groups(), ToolProfile.DIRECT, defers=True, natively=False
    )
    assert deferred == []
    assert {"spawn_subagent", "wait_for", "delegate"} <= _names(shown)


@pytest.mark.parametrize("natively", [True, False])
def test_core_cli_shows_only_the_core(natively: bool) -> None:
    shown, deferred = _arm(
        _all_groups(), ToolProfile.CORE_CLI, defers=True, natively=natively
    )
    assert _names(shown) == {
        "exec_command",
        "pod_query",
        "ask_user",
        "display_resource",
        "wait_for",
        "delegate",
    }
    assert deferred == []


def test_core_native_keeps_the_rest_behind_a_native_search_only() -> None:
    _, deferred = _arm(
        _all_groups(), ToolProfile.CORE_NATIVE, defers=True, natively=True
    )
    assert "pod_write_file" in _names(deferred)
    _, deferred = _arm(
        _all_groups(), ToolProfile.CORE_NATIVE, defers=True, natively=False
    )
    assert deferred == []


def test_only_the_pod_assistant_follows_the_deployment() -> None:
    deployed = ToolProfile.CORE_CLI
    assert run_tool_profile(RunKind.CHAT, pod_default=True, deployed=deployed) is (
        ToolProfile.CORE_CLI
    )
    assert run_tool_profile(RunKind.CHAT, pod_default=False, deployed=deployed) is (
        ToolProfile.TODAY
    )
    # A stranger's run and a voice call keep theirs.
    for kind in (RunKind.CONTACT, RunKind.CALL):
        assert run_tool_profile(kind, pod_default=True, deployed=deployed) is (
            ToolProfile.TODAY
        )
