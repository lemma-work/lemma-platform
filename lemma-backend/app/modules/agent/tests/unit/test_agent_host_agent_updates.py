"""What Lemma does with a harness report from an agent newer than itself.

The coding agents an Agent Host drives release weekly, and each release can add
a configuration option type, a permission option kind, a field. The rule (see
"Resilience to agent updates" in docs/architecture/agent-host.md): the host's
own protocol is held strictly, and whatever the *agent* added is tolerated --
dropped or ignored, never the reason the parts Lemma knows stop working.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.modules.agent.domain.agent_host import (
    AgentHostEvent,
    AgentHostHarnessSnapshot,
    AgentHostToolRef,
)
from app.modules.agent.domain.agent_host_link import HarnessesBody
from app.modules.agent.domain.agent_host_permissions import (
    agent_host_permission_request,
    permission_approval_tool_args,
)
from app.modules.agent.domain.agent_host_selections import (
    carry_agent_host_selections,
    validate_agent_host_model,
    validate_agent_host_selections,
)
from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision


def _snapshot(**overrides: object) -> dict[str, object]:
    return {
        "harness_key": "claude-code",
        "display_name": "Claude Code",
        "adapter_version": "0.62.0",
        "upstream_version": "2.1.220",
        "health": "READY",
        "config_revision": "rev-1",
        "stale_after": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        **overrides,
    }


_MODEL = {
    "id": "model",
    "category": "model",
    "name": "Model",
    "current_value": "sonnet",
    "options": [{"value": "sonnet", "name": "Sonnet"}, {"value": "opus"}],
    "metadata": {"type": "select"},
    "a_field_from_the_future": {"x": 1},
}
_EFFORT = {
    "id": "effort",
    "category": "thought_level",
    "name": "Effort",
    "current_value": "medium",
    "options": [{"value": "low"}, {"value": "medium"}, {"value": "high"}],
    "metadata": {"type": "select"},
}
_TEMPERATURE = {
    "id": "temperature",
    "category": "_vendor_sampling",
    "name": "Temperature",
    "current_value": 0.2,
    "metadata": {"type": "number", "minimum": 0, "maximum": 1},
}
_UNREADABLE = {"id": "", "category": "x", "name": "broken"}


class TestSnapshot:
    def test_an_unknown_option_type_is_dropped_and_the_rest_stands(self) -> None:
        snapshot = AgentHostHarnessSnapshot.model_validate(
            _snapshot(config_options=[_MODEL, _TEMPERATURE, _EFFORT])
        )

        assert [option.id for option in snapshot.config_options] == [
            "model",
            "effort",
        ]
        assert snapshot.skipped_config_options == ["type:number"]

    def test_one_unreadable_option_does_not_cost_the_harness(self) -> None:
        body = HarnessesBody.model_validate(
            {"harnesses": [_snapshot(config_options=[_UNREADABLE, _MODEL])]}
        )

        (snapshot,) = body.harnesses
        assert [option.id for option in snapshot.config_options] == ["model"]
        assert snapshot.skipped_config_options == ["invalid"]

    def test_extra_fields_are_ignored_everywhere_the_agent_writes(self) -> None:
        snapshot = AgentHostHarnessSnapshot.model_validate(
            _snapshot(
                config_options=[_MODEL],
                capabilities={"images": True, "a_capability_from_the_future": 1},
                a_snapshot_field_from_the_future="x",
            )
        )

        assert snapshot.capabilities.images is True
        assert snapshot.config_options[0].options[0]["value"] == "sonnet"

    def test_skipped_options_are_not_stored(self) -> None:
        snapshot = AgentHostHarnessSnapshot.model_validate(
            _snapshot(config_options=[_TEMPERATURE])
        )

        assert "skipped_config_options" not in snapshot.model_dump(mode="json")

    def test_a_host_cannot_claim_options_were_skipped(self) -> None:
        snapshot = AgentHostHarnessSnapshot.model_validate(
            _snapshot(skipped_config_options=["type:invented"])
        )

        assert snapshot.skipped_config_options == []

    def test_a_long_version_banner_is_cut_not_refused(self) -> None:
        snapshot = AgentHostHarnessSnapshot.model_validate(
            _snapshot(upstream_version="2.3.0 (Claude Code) " + "x" * 400)
        )

        assert snapshot.upstream_version is not None
        assert snapshot.upstream_version.startswith("2.3.0 (Claude Code)")
        assert len(snapshot.upstream_version) == 128

    def test_a_long_option_label_is_cut_not_refused(self) -> None:
        snapshot = AgentHostHarnessSnapshot.model_validate(
            _snapshot(config_options=[{**_MODEL, "name": "M" * 400}])
        )

        assert len(snapshot.config_options[0].name) == 255

    def test_the_hosts_own_envelope_stays_strict(self) -> None:
        """Health is the host's word, not the agent's: a value it never sends
        is a host bug, and refusing it is what keeps the two in step."""
        with pytest.raises(ValidationError):
            AgentHostHarnessSnapshot.model_validate(_snapshot(health="SLEEPY"))
        with pytest.raises(ValidationError):
            AgentHostEvent.model_validate(
                {
                    "run_id": "00000000-0000-0000-0000-000000000001",
                    "lease_epoch": 1,
                    "sequence": 1,
                    "type": "a_type_from_the_future",
                }
            )


class TestSelections:
    def test_the_unknown_option_is_not_offered_for_selection(self) -> None:
        stored = [_MODEL, _TEMPERATURE]

        with pytest.raises(RuntimeProfileValidationError):
            validate_agent_host_selections(
                config_options=stored, selections={"temperature": 0.5}
            )
        assert (
            carry_agent_host_selections(
                config_options=stored, selections={"temperature": 0.5}
            )
            == {}
        )

    def test_known_options_beside_it_still_work(self) -> None:
        stored = [_MODEL, _TEMPERATURE, _EFFORT]

        assert validate_agent_host_selections(
            config_options=stored, selections={"effort": "high"}
        ) == {"effort": "high"}
        assert (
            validate_agent_host_model(config_options=stored, model_name="opus")
            == "opus"
        )

    def test_a_selection_for_an_option_that_disappeared_is_dropped(self) -> None:
        republished = [_MODEL]

        assert (
            carry_agent_host_selections(
                config_options=republished,
                selections={"effort": "high", "temperature": 0.5},
            )
            == {}
        )

    def test_grouped_choices_are_choices(self) -> None:
        """A group is not a value; the values are inside it. Read as a value,
        an option of groups had no enumeration and took any scalar."""
        grouped = {
            "id": "mode",
            "category": "mode",
            "name": "Mode",
            "options": [
                {"group": "safe", "name": "Safe", "options": [{"value": "default"}]},
                {"group": "plan", "name": "Plan", "options": [{"value": "plan"}]},
            ],
        }

        assert validate_agent_host_selections(
            config_options=[grouped], selections={"mode": "plan"}
        ) == {"mode": "plan"}
        with pytest.raises(RuntimeProfileValidationError):
            validate_agent_host_selections(
                config_options=[grouped], selections={"mode": "anything"}
            )


class TestEvents:
    def test_a_tool_kind_longer_than_any_acp_defines_is_cut(self) -> None:
        tool = AgentHostToolRef.model_validate(
            {"name": "browse", "source": "native", "kind": "k" * 200, "new": 1}
        )

        assert tool.kind == "k" * 64

    def test_an_unknown_tool_source_is_still_the_hosts_bug(self) -> None:
        with pytest.raises(ValidationError):
            AgentHostToolRef.model_validate({"name": "x", "source": "elsewhere"})


class TestPermissionOptions:
    def _request(self, *options: dict[str, str]):
        parsed = agent_host_permission_request(
            permission_approval_tool_args({"options": list(options)}, request_id="r")
        )
        assert parsed is not None
        return parsed

    def test_an_unknown_kind_is_never_selected_as_an_approval(self) -> None:
        request = self._request(
            {"optionId": "forever", "kind": "allow_forever"},
            {"optionId": "no", "kind": "reject_once"},
        )

        assert request.option_for(AgentRunApprovalDecision.APPROVE_ONCE) is None
        assert request.option_for(AgentRunApprovalDecision.APPROVE_FOR_SESSION) is None

    def test_known_kinds_beside_an_unknown_one_still_work(self) -> None:
        request = self._request(
            {"optionId": "forever", "kind": "allow_forever"},
            {"optionId": "once", "kind": "allow_once"},
            {"optionId": "always", "kind": "allow_always"},
        )

        assert request.option_for(AgentRunApprovalDecision.APPROVE_ONCE) == "once"
        assert (
            request.option_for(AgentRunApprovalDecision.APPROVE_FOR_SESSION) == "always"
        )
