"""Which coding-agent selections a dispatched Agent Host run carries.

The profile's saved selections, then the run's own (pod default, teammate,
conversation -- layered when the turn started), narrowest per option; and of
those only what the harness offers for the run's model right now.
"""

from __future__ import annotations

from uuid import uuid7

from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.modules.agent.infrastructure.harnesses.agent_host.run_config import (
    agent_host_run_config,
    offered_selections,
)


def _options(model: str | None, overrides: dict[str, str]) -> HarnessOptions[object]:
    return HarnessOptions(
        model_name=model or "default",
        runtime_profile=RuntimeSnapshot(
            profile_id=str(uuid7()),
            profile_name="Laptop",
            harness_id=str(uuid7()),
            scope=RuntimeProfileScope.PERSONAL,
            protocol=RuntimeProfileProtocol.AGENT_HOST,
            model_name=model,
            provider_model_name=model,
            config={
                "harness_snapshot_revision": "rev-1",
                "config_selections": {"effort": "low", "mode": "plan"},
            },
            selection_overrides=overrides,
        ),
    )


_HARNESS = [
    {
        "id": "model",
        "category": "model",
        "options": [
            {"value": "sonnet"},
            {
                "value": "opus",
                "lemma_options": [
                    {
                        "id": "effort",
                        "category": "thought_level",
                        "options": [{"value": "low"}, {"value": "max"}],
                    },
                    {"id": "mode", "category": "mode", "options": [{"value": "plan"}]},
                ],
            },
        ],
    },
    {"id": "effort", "category": "thought_level", "options": [{"value": "low"}]},
    {"id": "mode", "category": "mode", "options": [{"value": "plan"}]},
]


def test_the_runs_own_selections_lay_over_the_profiles_per_option():
    config = agent_host_run_config(_options("opus", {"effort": "max"}))

    assert config.config_selections == {"effort": "max", "mode": "plan"}


def test_a_value_the_runs_model_offers_is_kept():
    config = agent_host_run_config(_options("opus", {"effort": "max"}))

    kept = offered_selections(config, config_options=_HARNESS, agent_run_id=uuid7())

    assert kept.config_selections == {"effort": "max", "mode": "plan"}


def test_a_value_the_runs_model_does_not_offer_is_dropped_not_refused():
    config = agent_host_run_config(_options("sonnet", {"effort": "max"}))

    kept = offered_selections(config, config_options=_HARNESS, agent_run_id=uuid7())

    assert kept.config_selections == {"mode": "plan"}
    assert kept.model_name == "sonnet"
