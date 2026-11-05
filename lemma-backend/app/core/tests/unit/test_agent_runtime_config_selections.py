"""A runtime's own coding-agent selections: how they are stored and layered.

``AgentRuntimeConfig.config_selections`` is set on a pod default, a teammate
and a conversation, and the run gets them layered narrowest-wins per option.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.domain.runtime import AgentRuntimeConfig


def test_selections_are_serialized_only_when_set():
    assert "config_selections" not in AgentRuntimeConfig(profile_id="p").model_dump()
    assert (
        "config_selections"
        not in AgentRuntimeConfig(profile_id="p", config_selections={}).model_dump()
    )
    assert AgentRuntimeConfig(
        profile_id="p", config_selections={" effort ": "max"}
    ).model_dump() == {"profile_id": "p", "config_selections": {"effort": "max"}}


def test_a_blank_option_id_is_refused():
    with pytest.raises(ValidationError):
        AgentRuntimeConfig(profile_id="p", config_selections={"  ": "max"})


def test_overrides_layer_selections_per_option():
    base = AgentRuntimeConfig(
        profile_id="p", config_selections={"effort": "low", "mode": "plan"}
    )

    layered = base.with_overrides(config_selections={"effort": "max"})

    assert layered.config_selections == {"effort": "max", "mode": "plan"}


def test_the_narrowest_runtime_wins_per_option_on_its_own_profile():
    pod = AgentRuntimeConfig(
        profile_id="laptop", config_selections={"effort": "low", "mode": "plan"}
    )
    teammate = AgentRuntimeConfig(
        profile_id="laptop", config_selections={"effort": "high"}
    )
    conversation = AgentRuntimeConfig(
        profile_id="laptop", config_selections={"effort": "max"}
    )

    assert conversation.layered_over([pod, teammate]).config_selections == {
        "effort": "max",
        "mode": "plan",
    }
    assert teammate.layered_over([pod, None]).config_selections == {
        "effort": "high",
        "mode": "plan",
    }


def test_selections_for_another_profile_do_not_leak_in():
    """An option id means something only to the harness it was chosen for."""
    pod = AgentRuntimeConfig(profile_id="desktop", config_selections={"mode": "plan"})
    conversation = AgentRuntimeConfig(profile_id="laptop")

    assert conversation.layered_over([pod]) is conversation
    assert conversation.layered_over([pod]).config_selections is None
