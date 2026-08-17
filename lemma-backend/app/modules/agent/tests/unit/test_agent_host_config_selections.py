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
    validate_agent_host_selections,
)


def _option(**overrides: object) -> dict[str, object]:
    return {"id": "permission_mode", "category": "permission", "name": "Mode", **overrides}


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
        _option(options=[{"id": "default"}, {"id": "plan"}, {"id": "bypassPermissions"}])
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
