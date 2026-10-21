"""Which harness configuration selections a profile may save.

The Agent Host owns the permission policy. It removes every value that would
turn off the approval gate from the options it publishes, and refuses one again
at session setup (``selection_is_allowed`` in
``desktop/agent-host/src/acp/options.rs``). So the check here is membership in
what the host published, and nothing more: a second rule set here used to
disagree with the host's, reading ``model`` as a mode.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.agent_host_selections import (
    carry_agent_host_model,
    carry_agent_host_selections,
    options_for_model,
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

    with pytest.raises(RuntimeProfileValidationError, match="Invalid value"):
        validate_agent_host_selections(
            config_options=options, selections={"permission_mode": "plan9"}
        )


def test_membership_in_what_the_host_published_is_the_whole_check():
    """The host publishes only the values it allows; `bypassPermissions` never
    reaches this list from a real host, and plan mode does."""
    options = [_option(options=[{"id": "default"}, {"id": "plan"}])]

    assert validate_agent_host_selections(
        config_options=options, selections={"permission_mode": "plan"}
    ) == {"permission_mode": "plan"}
    with pytest.raises(RuntimeProfileValidationError, match="Invalid value"):
        validate_agent_host_selections(
            config_options=options,
            selections={"permission_mode": "bypassPermissions"},
        )


def test_an_option_whose_name_merely_contains_mode_is_not_policy():
    """The substring rule this replaced read `model_mode` -- and `model` --
    as a permission mode."""
    assert validate_agent_host_selections(
        config_options=[{"id": "reasoning_mode", "category": "thought_level"}],
        selections={"reasoning_mode": "auto"},
    ) == {"reasoning_mode": "auto"}


def test_the_model_category_is_rejected_outright():
    """Models go through default_model_name, which validates against the
    harness's own catalog. Accepting one here would bypass that."""
    with pytest.raises(RuntimeProfileValidationError, match="default_model_name"):
        validate_agent_host_selections(
            config_options=[{"id": "model", "category": "model"}],
            selections={"model": "gpt-5.1"},
        )


def test_an_unknown_selection_is_rejected():
    with pytest.raises(RuntimeProfileValidationError, match="Unknown"):
        validate_agent_host_selections(
            config_options=[_option()], selections={"nope": "x"}
        )


@pytest.mark.parametrize("category", ["mode", "collaboration_mode"])
def test_a_mode_the_host_published_may_be_chosen(category):
    """Plan mode is a person's choice. The host has already removed the values
    that would skip approvals, so what it publishes may be selected."""
    options = [
        {"id": "session_mode", "category": category, "options": [{"id": "plan"}]}
    ]

    assert validate_agent_host_selections(
        config_options=options, selections={"session_mode": "plan"}
    ) == {"session_mode": "plan"}


def test_a_selection_may_be_keyed_by_category_instead_of_id():
    """The harness option index resolves a selection by either name, because a
    caller may have saved against the category rather than the specific id."""
    options = [{"id": "workdir_v2", "category": "workdir", "options": None}]

    assert validate_agent_host_selections(
        config_options=options, selections={"workdir": "/repo"}
    ) == {"workdir": "/repo"}


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"nested": "object"}, id="object"),
        pytest.param(["a", "list"], id="list"),
        pytest.param(None, id="null"),
    ],
)
def test_an_option_without_an_enumeration_takes_only_a_scalar(value: object):
    """Nothing was published to check membership against, and anything at all
    used to be saved and forwarded to the host as the setting."""
    options = [{"id": "workdir", "category": "workdir", "options": []}]

    with pytest.raises(RuntimeProfileValidationError, match="Invalid value"):
        validate_agent_host_selections(
            config_options=options, selections={"workdir": value}
        )
    assert (
        carry_agent_host_selections(
            config_options=options, selections={"workdir": value}
        )
        == {}
    )


@pytest.mark.parametrize(
    ("current", "value", "accepted"),
    [
        pytest.param(True, False, True, id="toggle takes a bool"),
        pytest.param(True, 1, False, id="toggle refuses a number"),
        pytest.param(30, True, False, id="number refuses a bool"),
        pytest.param(30, 45.5, True, id="number takes a number"),
        pytest.param("auto", 3, False, id="text refuses a number"),
    ],
)
def test_an_unenumerated_value_matches_the_type_the_harness_reports(
    current: object, value: object, accepted: bool
):
    options = [{"id": "knob", "category": "knob", "current_value": current}]

    if accepted:
        assert validate_agent_host_selections(
            config_options=options, selections={"knob": value}
        ) == {"knob": value}
    else:
        with pytest.raises(RuntimeProfileValidationError, match="Invalid value"):
            validate_agent_host_selections(
                config_options=options, selections={"knob": value}
            )


class TestValidateAgentHostModel:
    def test_none_stays_none(self):
        assert validate_agent_host_model(config_options=[], model_name=None) is None

    def test_blank_name_is_rejected(self):
        with pytest.raises(RuntimeProfileValidationError, match="cannot be empty"):
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
        with pytest.raises(RuntimeProfileValidationError, match="not offered"):
            validate_agent_host_model(config_options=options, model_name="gpt-9000")

    def test_no_model_option_at_all_is_rejected(self):
        """A harness that publishes no `model` category has nothing to pin
        against, so any requested name is unavailable -- not a pass-through."""
        with pytest.raises(RuntimeProfileValidationError, match="not offered"):
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


def _effort(*values: str) -> dict[str, object]:
    return {
        "id": "effort",
        "category": "thought_level",
        "name": "Effort",
        "options": [{"value": value, "name": value.title()} for value in values],
    }


def _claude_like() -> list[object]:
    """A harness whose default model tops out at ``high`` and whose ``opus``
    also offers ``max`` -- the per-model contract, ``lemma_options`` on the
    model's own entry."""
    return [
        {
            "id": "model",
            "category": "model",
            "name": "Model",
            "options": [
                {"value": "sonnet", "name": "Sonnet"},
                {
                    "value": "opus",
                    "name": "Opus",
                    "lemma_options": [_effort("low", "high", "max")],
                },
            ],
        },
        _effort("low", "high"),
    ]


class TestOptionsForModel:
    def test_no_model_is_the_harness_default_and_gets_the_top_level(self):
        assert options_for_model(_claude_like(), None) == [_effort("low", "high")]
        assert options_for_model(_claude_like(), "  ") == [_effort("low", "high")]

    def test_a_model_with_its_own_options_gets_those(self):
        assert options_for_model(_claude_like(), "opus") == [
            _effort("low", "high", "max")
        ]

    def test_a_model_without_its_own_options_gets_the_top_level(self):
        assert options_for_model(_claude_like(), "sonnet") == [_effort("low", "high")]

    def test_a_model_the_harness_does_not_list_gets_the_top_level(self):
        assert options_for_model(_claude_like(), "retired") == [_effort("low", "high")]

    def test_the_model_option_itself_is_never_a_selection(self):
        nested_model = _claude_like()
        nested_model[0]["options"][1]["lemma_options"].append(
            {"id": "model", "category": "model", "options": []}
        )
        assert all(
            option["category"] != "model"
            for option in options_for_model(nested_model, "opus")
        )


class TestSelectionsFollowTheChosenModel:
    def test_a_value_only_the_chosen_model_offers_is_accepted(self):
        """``max`` is refused against the machine's default model and accepted
        against the one that offers it."""
        assert validate_agent_host_selections(
            config_options=_claude_like(),
            selections={"effort": "max"},
            model_name="opus",
        ) == {"effort": "max"}
        with pytest.raises(RuntimeProfileValidationError, match="Invalid value"):
            validate_agent_host_selections(
                config_options=_claude_like(),
                selections={"effort": "max"},
                model_name="sonnet",
            )
        with pytest.raises(RuntimeProfileValidationError, match="Invalid value"):
            validate_agent_host_selections(
                config_options=_claude_like(), selections={"effort": "max"}
            )

    def test_the_model_is_still_refused_as_a_selection(self):
        with pytest.raises(RuntimeProfileValidationError, match="default_model_name"):
            validate_agent_host_selections(
                config_options=_claude_like(),
                selections={"model": "opus"},
                model_name="opus",
            )

    def test_carrying_drops_what_the_model_the_run_uses_does_not_offer(self):
        assert carry_agent_host_selections(
            config_options=_claude_like(),
            selections={"effort": "max"},
            model_name="opus",
        ) == {"effort": "max"}
        assert (
            carry_agent_host_selections(
                config_options=_claude_like(),
                selections={"effort": "max", "model": "opus"},
                model_name=None,
            )
            == {}
        )
