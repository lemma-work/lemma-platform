"""Which harness configuration selections a profile may save.

Agent Host keeps its own deny-list and refuses a policy-bearing selection when
it sets up the session (``selection_is_allowed`` in ``desktop/agent-host/src/acp.rs``),
so nothing here is a privilege boundary. It is a timing fix: without it a value
the host will refuse saves cleanly and only fails on the user's first run, far
from the dialog that accepted it.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.agent_host_selections import (
    AgentHostSelectionRefused,
    carry_agent_host_model,
    carry_agent_host_selections,
    validate_agent_host_model,
    validate_agent_host_selections,
)


def _option(**overrides: object) -> dict[str, object]:
    return {
        "id": "permission_mode",
        "category": "permission",
        "name": "Mode",
        **overrides,
    }


def test_an_enumerated_option_still_enforces_membership():
    options = [_option(options=[{"id": "ask"}, {"id": "plan"}])]

    assert validate_agent_host_selections(
        config_options=options, selections={"permission_mode": "ask"}
    ) == {"permission_mode": "ask"}

    with pytest.raises(ValueError, match="Invalid value"):
        validate_agent_host_selections(
            config_options=options, selections={"permission_mode": "plan9"}
        )


def test_the_deny_list_beats_the_harness_own_option_list():
    """The case that actually happens.

    Harnesses enumerate their permission modes - Claude Code lists
    ``bypassPermissions`` among its own - so this value passes a membership
    check. The host refuses it regardless (``selection_is_allowed`` tests the
    deny-list first), so checking membership first here would let precisely the
    common case save and then fail at session setup.
    """
    options = [
        _option(
            options=[{"id": "default"}, {"id": "plan"}, {"id": "bypassPermissions"}]
        )
    ]

    with pytest.raises(ValueError, match="not allowed"):
        validate_agent_host_selections(
            config_options=options,
            selections={"permission_mode": "bypassPermissions"},
        )

    # The safe members of the very same list still go through.
    assert validate_agent_host_selections(
        config_options=options, selections={"permission_mode": "plan"}
    ) == {"permission_mode": "plan"}


@pytest.mark.parametrize(
    "value",
    [
        "bypassPermissions",
        "bypass-permissions",
        "acceptEdits",
        "YOLO",
        "agentFullAccess",
        "auto",
    ],
)
def test_a_policy_option_refuses_a_free_form_escalating_value(value):
    """An option that advertises no values used to accept anything at all."""
    with pytest.raises(ValueError):
        validate_agent_host_selections(
            config_options=[_option()], selections={"permission_mode": value}
        )


def test_an_ordinary_free_form_value_is_still_accepted():
    """The rule targets policy options, not every option without a value list —
    a harness is free to expose settings Lemma has no opinion about."""
    assert validate_agent_host_selections(
        config_options=[{"id": "workdir", "category": "path"}],
        selections={"workdir": "auto"},
    ) == {"workdir": "auto"}

    assert validate_agent_host_selections(
        config_options=[_option()], selections={"permission_mode": "ask"}
    ) == {"permission_mode": "ask"}


def test_the_model_category_is_rejected_outright():
    """Models go through default_model_name, which validates against the
    harness's own catalog. Accepting one here would bypass that."""
    with pytest.raises(ValueError, match="default_model_name"):
        validate_agent_host_selections(
            config_options=[{"id": "model", "category": "model"}],
            selections={"model": "gpt-5.1"},
        )


def test_an_unknown_selection_is_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        validate_agent_host_selections(
            config_options=[_option()], selections={"nope": "x"}
        )


@pytest.mark.parametrize("category", ["mode", "collaboration_mode"])
def test_platform_owned_categories_are_dropped_not_rejected(category):
    """`mode` (the approval/sandbox preset) and `collaboration_mode` are
    Lemma's, not a per-profile choice. A stored value in one of these must not
    fail the whole save -- it is silently dropped, and the harness applies its
    own default, so a profile saved before this rule existed stays editable."""
    options = [{"id": "session_mode", "category": category}]

    assert (
        validate_agent_host_selections(
            config_options=options, selections={"session_mode": "whatever"}
        )
        == {}
    )


def test_a_selection_may_be_keyed_by_category_instead_of_id():
    """The harness option index resolves a selection by either name, because a
    caller may have saved against the category rather than the specific id."""
    options = [{"id": "workdir_v2", "category": "workdir", "options": None}]

    assert validate_agent_host_selections(
        config_options=options, selections={"workdir": "/repo"}
    ) == {"workdir": "/repo"}


class TestValidateAgentHostModel:
    def test_none_stays_none(self):
        assert validate_agent_host_model(config_options=[], model_name=None) is None

    def test_blank_name_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_agent_host_model(
                config_options=[{"id": "model", "category": "model", "options": []}],
                model_name="   ",
            )

    def test_a_name_the_harness_does_not_offer_is_rejected(self):
        options = [
            {
                "id": "model",
                "category": "model",
                "options": [{"id": "gpt-5.1"}],
            }
        ]
        with pytest.raises(ValueError, match="not offered"):
            validate_agent_host_model(config_options=options, model_name="gpt-9000")

    def test_no_model_option_at_all_is_rejected(self):
        """A harness that publishes no `model` category has nothing to pin
        against, so any requested name is unavailable -- not a pass-through."""
        with pytest.raises(ValueError, match="not offered"):
            validate_agent_host_model(
                config_options=[{"id": "workdir", "category": "path"}],
                model_name="gpt-5.1",
            )

    def test_an_offered_name_is_returned_stripped(self):
        options = [{"id": "model", "category": "model", "options": [{"id": "gpt-5.1"}]}]
        assert (
            validate_agent_host_model(config_options=options, model_name="  gpt-5.1  ")
            == "gpt-5.1"
        )


class TestCarryAgentHostSelections:
    """The lenient sibling: a run already in flight, not a user pressing Save."""

    def test_an_unknown_key_is_dropped_not_raised(self):
        assert (
            carry_agent_host_selections(
                config_options=[_option()], selections={"nope": "x"}
            )
            == {}
        )

    def test_a_value_the_republished_harness_no_longer_offers_is_dropped(self):
        options = [_option(options=[{"id": "ask"}, {"id": "plan"}])]

        assert (
            carry_agent_host_selections(
                config_options=options,
                selections={"permission_mode": "retired-value"},
            )
            == {}
        )

    def test_a_still_valid_selection_is_carried_over(self):
        options = [_option(options=[{"id": "ask"}, {"id": "plan"}])]

        assert carry_agent_host_selections(
            config_options=options, selections={"permission_mode": "plan"}
        ) == {"permission_mode": "plan"}

    def test_platform_owned_categories_are_dropped(self):
        options = [{"id": "session_mode", "category": "mode"}]

        assert (
            carry_agent_host_selections(
                config_options=options, selections={"session_mode": "anything"}
            )
            == {}
        )

    def test_a_policy_escalating_value_still_refuses_rather_than_drops(self):
        """Unlike an unknown/retired value, a policy-bearing escalation is not
        news about the harness changing -- it still must not silently ride
        along into a dispatched run, so this raises instead of dropping."""
        options = [_option(options=[{"id": "default"}, {"id": "bypassPermissions"}])]

        with pytest.raises(AgentHostSelectionRefused):
            carry_agent_host_selections(
                config_options=options,
                selections={"permission_mode": "bypassPermissions"},
            )


class TestCarryAgentHostModel:
    """Unlike `validate_agent_host_model`, this never raises -- a pinned model
    is a preference, and a run in flight falls back to the harness default
    rather than failing over a model the harness stopped offering."""

    def test_none_stays_none(self):
        assert carry_agent_host_model(config_options=[], model_name=None) is None

    def test_a_still_offered_model_is_kept(self):
        options = [{"id": "model", "category": "model", "options": [{"id": "gpt-5.1"}]}]
        assert (
            carry_agent_host_model(config_options=options, model_name="gpt-5.1")
            == "gpt-5.1"
        )

    def test_a_model_the_harness_dropped_falls_back_to_none_instead_of_raising(self):
        options = [{"id": "model", "category": "model", "options": [{"id": "gpt-5.1"}]}]
        assert (
            carry_agent_host_model(config_options=options, model_name="retired-model")
            is None
        )

    def test_no_model_option_at_all_falls_back_to_none(self):
        assert (
            carry_agent_host_model(
                config_options=[{"id": "workdir", "category": "path"}],
                model_name="gpt-5.1",
            )
            is None
        )
