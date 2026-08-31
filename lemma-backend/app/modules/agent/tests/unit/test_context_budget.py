"""A run's compaction thresholds come from its own model's window.

Two global constants used to decide this for every model at once. They were
wrong in both directions: a 70k trigger compacted a run on a million-token model
that had 900k to spare, and a 110k "ceiling" on a 128k model left no room for
the tool schemas, instructions and reply that share the same window.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.value_objects import (
    DEFAULT_HISTORY_HARD_TOKEN_CEILING,
    DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT,
)
from app.modules.agent.services.context_budget import (
    HARD_CEILING_FRACTION,
    SUMMARIZATION_FRACTION,
    ContextBudget,
    context_budget_for,
    resolve_context_window,
)

pytestmark = pytest.mark.unit


class _Entry:
    """The shape `ResolvedAgentRuntime.model` carries."""

    def __init__(self, metadata: dict[str, object], name: str = "a-model") -> None:
        self.metadata = metadata
        self.name = name


class TestWhereTheWindowComesFrom:
    def test_the_model_declares_its_own_window(self) -> None:
        assert (
            resolve_context_window(_Entry({"context_window": 1_000_000})) == 1_000_000
        )

    def test_operator_metadata_may_be_a_string(self) -> None:
        """Catalog metadata is hand-authored JSON; 128000 and "128000" are the
        same intent and a quoted number must not silently mean 'unset'."""
        assert resolve_context_window(_Entry({"context_window": "128000"})) == 128_000

    def test_a_model_that_declares_nothing_gets_the_deployment_default(self) -> None:
        assert resolve_context_window(_Entry({})) == _default_window()

    def test_no_model_at_all_still_yields_a_budget(self) -> None:
        """A harness profile routinely pins no model."""
        assert resolve_context_window(None) == _default_window()

    def test_an_env_override_moves_the_default(self, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_DEFAULT_CONTEXT_WINDOW_TOKENS", "300000")

        assert resolve_context_window(_Entry({})) == 300_000

    def test_the_model_wins_over_the_env_default(self, monkeypatch) -> None:
        """Most specific wins: the env sets a default, not an override."""
        monkeypatch.setenv("AGENT_DEFAULT_CONTEXT_WINDOW_TOKENS", "300000")

        assert resolve_context_window(_Entry({"context_window": 128_000})) == 128_000


class TestBadValuesDegradeLoudly:
    def test_an_unparsable_window_falls_back(self) -> None:
        assert resolve_context_window(_Entry({"context_window": "lots"})) == (
            _default_window()
        )

    def test_a_nonsense_window_falls_back(self) -> None:
        """Zero and negatives are not small models, they are bad values."""
        for bad in (0, -1, 12, True):
            assert resolve_context_window(_Entry({"context_window": bad})) == (
                _default_window()
            )

    def test_a_bad_env_value_falls_back_to_settings(self, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_DEFAULT_CONTEXT_WINDOW_TOKENS", "plenty")

        assert resolve_context_window(_Entry({})) == _default_window()

    def test_a_bad_settings_value_falls_back_to_the_built_in(self, monkeypatch) -> None:
        """A settings file is no more careful than an env var, so the same
        coercion has to cover both routes to the same number."""
        from app.modules.agent.services import context_budget

        monkeypatch.delenv("AGENT_DEFAULT_CONTEXT_WINDOW_TOKENS", raising=False)
        monkeypatch.setattr(
            context_budget.agent_settings, "agent_default_context_window_tokens", 12
        )

        assert resolve_context_window(_Entry({})) == (
            context_budget.DEFAULT_CONTEXT_WINDOW_TOKENS
        )


class TestTheDerivedBudget:
    def test_thresholds_leave_headroom_inside_the_window(self) -> None:
        """The prompt is not the whole request: tool schemas, instructions and
        the model's reply draw on the same window, and our count is an estimate
        rather than the provider's exact vocabulary."""
        budget = context_budget_for(_Entry({"context_window": 200_000}))

        assert budget.summarization_token_limit < budget.hard_token_ceiling
        assert budget.hard_token_ceiling < budget.window

    def test_a_bigger_window_buys_a_bigger_budget(self) -> None:
        small = context_budget_for(_Entry({"context_window": 128_000}))
        large = context_budget_for(_Entry({"context_window": 1_000_000}))

        assert large.summarization_token_limit > small.summarization_token_limit
        assert large.hard_token_ceiling > small.hard_token_ceiling

    def test_a_two_hundred_k_model_may_actually_work_to_two_hundred_k(self) -> None:
        """The point of the exercise: an agent on a 200k model should compact
        near 200k, not at 70k."""
        budget = context_budget_for(_Entry({"context_window": 200_000}))

        assert budget.summarization_token_limit == 160_000
        assert budget.hard_token_ceiling == 184_000

    def test_the_standalone_defaults_match_the_default_window(self) -> None:
        """`HarnessOptions` built by hand must not describe a different policy
        from the one a real run resolves."""
        window = _default_window()

        assert DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT == int(
            window * SUMMARIZATION_FRACTION
        )
        assert DEFAULT_HISTORY_HARD_TOKEN_CEILING == int(window * HARD_CEILING_FRACTION)

    def test_the_budget_is_a_value_not_a_mutable(self) -> None:
        budget = context_budget_for(None)

        assert isinstance(budget, ContextBudget)
        with pytest.raises(Exception):
            budget.window = 1  # type: ignore[misc]


def _default_window() -> int:
    from app.modules.agent.config import agent_settings

    return agent_settings.agent_default_context_window_tokens


class TestWindowsRecordedWhenModelsAreRegistered:
    """A window nobody records is a window nobody can use.

    The budget resolver reads `metadata.context_window` off a model's catalog
    entry, and every writer of those entries used to leave metadata empty -- so
    every model in every deployment fell back to the default, however large its
    real window.
    """

    def test_an_operator_declaration_is_recorded(self, monkeypatch) -> None:
        from app.modules.agent.services.context_budget import (
            CONTEXT_WINDOW_METADATA_KEY,
            catalog_metadata_for,
        )

        monkeypatch.setenv(
            "AGENT_MODEL_CONTEXT_WINDOWS", "claude-sonnet-4=200000,kimi-k3=131072"
        )

        assert catalog_metadata_for("kimi-k3") == {CONTEXT_WINDOW_METADATA_KEY: 131072}

    def test_a_model_nobody_declared_carries_nothing(self, monkeypatch) -> None:
        """Empty, not a guess: the deployment default then applies, and a wrong
        window is worse than an admitted unknown."""
        from app.modules.agent.services.context_budget import catalog_metadata_for

        monkeypatch.setenv("AGENT_MODEL_CONTEXT_WINDOWS", "kimi-k3=131072")

        assert catalog_metadata_for("some-other-model") == {}

    def test_what_the_provider_advertised_is_used(self, monkeypatch) -> None:
        from app.modules.agent.services.context_budget import (
            CONTEXT_WINDOW_METADATA_KEY,
            catalog_metadata_for,
        )

        monkeypatch.delenv("AGENT_MODEL_CONTEXT_WINDOWS", raising=False)

        assert catalog_metadata_for("m", discovered_window=64_000) == {
            CONTEXT_WINDOW_METADATA_KEY: 64_000
        }

    def test_an_operator_overrides_the_provider(self, monkeypatch) -> None:
        """They are correcting it on purpose."""
        from app.modules.agent.services.context_budget import (
            CONTEXT_WINDOW_METADATA_KEY,
            catalog_metadata_for,
        )

        monkeypatch.setenv("AGENT_MODEL_CONTEXT_WINDOWS", "m=200000")

        assert catalog_metadata_for("m", discovered_window=64_000) == {
            CONTEXT_WINDOW_METADATA_KEY: 200_000
        }

    def test_one_bad_pair_does_not_cost_the_others(self, monkeypatch) -> None:
        """A typo must not take a deployment's whole catalog down with it."""
        from app.modules.agent.services.context_budget import (
            configured_model_context_windows,
        )

        monkeypatch.setenv(
            "AGENT_MODEL_CONTEXT_WINDOWS", "good=131072,broken,also-broken=lots"
        )

        assert configured_model_context_windows() == {"good": 131072}

    def test_a_declared_window_reaches_the_budget(self, monkeypatch) -> None:
        """End to end: declaring it must actually change what a run may spend."""
        monkeypatch.setenv("AGENT_MODEL_CONTEXT_WINDOWS", "big=1000000")
        from app.modules.agent.services.context_budget import catalog_metadata_for

        budget = context_budget_for(_Entry(catalog_metadata_for("big"), name="big"))

        assert budget.window == 1_000_000
        assert budget.summarization_token_limit == 800_000


class TestDiscoveryReadsTheWindowFromTheProvider:
    def _parse(self, payload: dict):
        from app.modules.agent.services.runtime_provider_discovery import (
            _parse_openai_compatible_models,
        )

        return _parse_openai_compatible_models(payload)

    def test_context_length_is_read(self) -> None:
        (model,) = self._parse({"data": [{"id": "m", "context_length": 131072}]})

        assert model.context_window == 131072

    def test_the_openrouter_nested_form_is_read(self) -> None:
        (model,) = self._parse(
            {"data": [{"id": "m", "top_provider": {"context_length": 200000}}]}
        )

        assert model.context_window == 200_000

    def test_the_vllm_spelling_is_read(self) -> None:
        (model,) = self._parse({"data": [{"id": "m", "max_model_len": 32768}]})

        assert model.context_window == 32768

    def test_a_payload_that_says_nothing_yields_nothing(self) -> None:
        """The standard OpenAI schema carries no window at all."""
        (model,) = self._parse({"data": [{"id": "m"}]})

        assert model.context_window is None

    def test_a_nonsense_value_is_ignored(self) -> None:
        (model,) = self._parse({"data": [{"id": "m", "context_length": "lots"}]})

        assert model.context_window is None
