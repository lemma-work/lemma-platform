"""Generation settings: four layers, one translation, and the REASONING gate.

A run's settings come from the model's catalog entry, the runtime profile, the
agent and the workflow step, in that order. Effort reaches the wire only for a
model that declares it reasons: OPENAI_COMPATIBLE is baseline Chat
Completions, and a non-reasoning model rejects ``reasoning_effort``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.core.domain.generation import GenerationSettings, ReasoningEffort
from app.core.domain.runtime import AgentRuntimeConfig
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    AnthropicCompatibleRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    with_reasoning_declared,
)
from app.modules.agent.domain.value_objects import HarnessKind, JsonObject
from app.modules.agent.services.context_budget import context_budget_for
from app.modules.agent.services.generation_settings import (
    COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS,
    compaction_generation_settings,
    record_generation_settings,
    run_generation_settings,
    unreasoned_model_settings,
    validated_stored_settings,
)
from app.modules.agent.services.run_input_settings import (
    shorter_answer_settings,
    with_reply_budget,
)
from app.modules.agent.services.runtime_profile_creation import (
    normalize_model_settings,
)
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime

_REASONS = [RuntimeModelCapability.TEXT, RuntimeModelCapability.REASONING]
_PLAIN = [RuntimeModelCapability.TEXT]


def _resolved(
    *,
    protocol: RuntimeProfileProtocol = RuntimeProfileProtocol.OPENAI_COMPATIBLE,
    capabilities: list[RuntimeModelCapability] | None = None,
    catalog_defaults: JsonObject | None = None,
    profile_settings: JsonObject | None = None,
) -> ResolvedAgentRuntime:
    model = RuntimeModelCatalogEntry(
        name="m",
        provider_model_name="m",
        capabilities=_REASONS if capabilities is None else capabilities,
        default_model_settings=catalog_defaults or {},
    )
    config = (
        OpenAICompatibleRuntimeConfig(
            base_url="https://models.test/v1", model_settings=profile_settings or {}
        )
        if protocol is RuntimeProfileProtocol.OPENAI_COMPATIBLE
        else AnthropicCompatibleRuntimeConfig(model_settings=profile_settings or {})
    )
    profile = AgentRuntimeProfile(
        id="p",
        organization_id="00000000-0000-0000-0000-000000000001",
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=protocol,
        name="Provider",
        default_model_name="m",
        model_catalog=[model],
        config=config,
    )
    return ResolvedAgentRuntime(
        profile=profile,
        harness_kind=HarnessKind.LEMMA,
        model=model,
        provider_model_name="m",
        credentials=None,
    )


def _runtime(**settings: object) -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        profile_id="p", settings=GenerationSettings.model_validate(settings)
    )


class TestPrecedence:
    def test_each_layer_overrides_the_one_below(self):
        resolved = _resolved(
            catalog_defaults={"reasoning_effort": "high", "temperature": 0.1},
            profile_settings={"reasoning_effort": "medium", "top_p": 0.9},
        )

        settings = run_generation_settings(resolved, _runtime(reasoning_effort="low"))

        assert settings.model_settings == {
            "openai_reasoning_effort": "low",
            "temperature": 0.1,
            "top_p": 0.9,
        }
        assert settings.effective == GenerationSettings(
            reasoning_effort=ReasoningEffort.LOW, temperature=0.1, top_p=0.9
        )

    def test_a_step_override_wins_over_the_agent(self):
        agent = _runtime(reasoning_effort="high", max_output_tokens=4000)
        step = agent.with_overrides(
            settings=GenerationSettings(reasoning_effort=ReasoningEffort.MINIMAL)
        )

        settings = run_generation_settings(_resolved(), step)

        assert settings.model_settings == {
            "openai_reasoning_effort": "minimal",
            "max_tokens": 4000,
        }

    def test_a_portable_cap_outranks_a_legacy_wire_cap_below_it(self):
        """`max_tokens` and `max_output_tokens` are one parameter spelled two
        ways; the higher layer wins whichever spelling each used."""
        resolved = _resolved(profile_settings={"max_tokens": 9000})

        settings = run_generation_settings(resolved, _runtime(max_output_tokens=500))

        assert settings.model_settings["max_tokens"] == 500

    def test_operator_wire_switches_pass_through(self):
        resolved = _resolved(
            profile_settings={"openai_continuous_usage_stats": True},
            capabilities=_PLAIN,
        )

        settings = run_generation_settings(resolved, None)

        assert settings.model_settings == {"openai_continuous_usage_stats": True}

    def test_no_settings_anywhere_sends_nothing(self):
        """Nothing set must stay nothing sent: no default is flipped in code."""
        assert run_generation_settings(_resolved(), None).model_settings == {}


class TestReasoningGate:
    def test_effort_is_dropped_for_a_model_that_does_not_reason(self):
        resolved = _resolved(capabilities=_PLAIN)

        settings = run_generation_settings(
            resolved, _runtime(reasoning_effort="low", temperature=0.3)
        )

        assert settings.model_settings == {"temperature": 0.3}

    def test_anthropic_gets_a_thinking_budget_only_when_the_model_reasons(self):
        anthropic = RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE
        runtime = _runtime(thinking_budget_tokens=2048, reasoning_effort="high")

        reasons = run_generation_settings(_resolved(protocol=anthropic), runtime)
        plain = run_generation_settings(
            _resolved(protocol=anthropic, capabilities=_PLAIN), runtime
        )

        assert reasons.model_settings == {
            "anthropic_thinking": {"type": "enabled", "budget_tokens": 2048}
        }
        assert plain.model_settings == {}

    def test_an_agent_host_profile_gets_no_wire_settings(self):
        resolved = _resolved()
        resolved.profile = resolved.profile.model_copy(
            update={"protocol": RuntimeProfileProtocol.AGENT_HOST}
        )

        settings = run_generation_settings(resolved, _runtime(max_output_tokens=10))

        assert settings.model_settings == {}

    def test_a_title_asks_every_model_for_no_effort_unless_told_not_to(self):
        none = {"openai_reasoning_effort": "none"}
        assert unreasoned_model_settings(_resolved()) == none
        # An undeclared reasoning model is the one that would think at length.
        assert unreasoned_model_settings(_resolved(capabilities=_PLAIN)) == none
        assert unreasoned_model_settings(_resolved(), ask_no_reasoning=False) == {}


class TestValidation:
    def test_a_profile_saved_with_a_bad_value_is_refused(self):
        with pytest.raises(RuntimeProfileValidationError):
            normalize_model_settings({"temperature": "hot"})
        with pytest.raises(RuntimeProfileValidationError):
            normalize_model_settings({"reasoning_effort": "extreme"})
        with pytest.raises(RuntimeProfileValidationError):
            normalize_model_settings({"max_tokens": "lots"})

    def test_good_values_are_kept_as_written(self):
        stored = {"reasoning_effort": "low", "openai_continuous_usage_stats": True}

        assert validated_stored_settings(stored) == stored

    def test_a_catalog_default_is_checked_where_it_is_written(self):
        with pytest.raises(ValidationError):
            RuntimeModelCatalogEntry(
                name="m",
                provider_model_name="m",
                default_model_settings={"top_p": 7},
            )

    def test_unknown_fields_are_refused_on_an_agent(self):
        with pytest.raises(ValidationError):
            AgentRuntimeConfig.model_validate(
                {"profile_id": "p", "settings": {"reasoning": "low"}}
            )

    def test_a_thinking_budget_must_leave_room_to_answer(self):
        with pytest.raises(ValidationError):
            GenerationSettings(max_output_tokens=1000, thinking_budget_tokens=1000)


class TestStoredShape:
    def test_unset_settings_are_not_written(self):
        """Agent and run rows predate the field; an absent field must read back
        and write out exactly as before."""
        assert AgentRuntimeConfig(profile_id="p").model_dump(mode="json") == {
            "profile_id": "p"
        }
        assert _runtime(reasoning_effort="low").model_dump(mode="json") == {
            "profile_id": "p",
            "settings": {"reasoning_effort": "low"},
        }

    def test_an_empty_settings_object_reads_as_unset(self):
        assert AgentRuntimeConfig(profile_id="p", settings={}).settings is None

    def test_a_conversation_that_picked_a_model_keeps_the_agents_settings(self):
        agent = _runtime(reasoning_effort="low")
        picked = AgentRuntimeConfig(profile_id="other", model_name="big")

        assert picked.inheriting_settings_from(agent).settings == agent.settings
        own = _runtime(reasoning_effort="high")
        assert own.inheriting_settings_from(agent).settings == own.settings

    def test_a_step_model_keeps_the_agents_profile(self):
        step = _runtime(reasoning_effort="low").with_overrides(model_name="small")

        assert (step.profile_id, step.model_name) == ("p", "small")
        assert step.settings == GenerationSettings(reasoning_effort=ReasoningEffort.LOW)


class TestBudgetAndCompaction:
    def test_a_chosen_cap_is_clamped_to_the_room_the_window_leaves(self):
        budget = context_budget_for(None)
        too_big = budget.reply_token_budget + 1000

        assert with_reply_budget({"max_tokens": too_big}, budget) == {
            "max_tokens": budget.reply_token_budget
        }
        assert with_reply_budget({"max_tokens": 64}, budget) == {"max_tokens": 64}

    def test_compaction_turns_effort_down_and_caps_the_summary(self):
        merged = GenerationSettings(
            reasoning_effort=ReasoningEffort.HIGH,
            thinking_budget_tokens=4000,
            temperature=0.2,
        )

        summary = compaction_generation_settings(merged)

        assert summary == GenerationSettings(
            reasoning_effort=ReasoningEffort.LOW,
            max_output_tokens=COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS,
            temperature=0.2,
        )
        assert (
            compaction_generation_settings(
                GenerationSettings(max_output_tokens=100)
            ).max_output_tokens
            == 100
        )

    def test_a_run_carries_compaction_settings_without_its_thinking(self):
        anthropic = _resolved(protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE)

        settings = run_generation_settings(
            anthropic, _runtime(thinking_budget_tokens=2048)
        )

        assert settings.compaction_model_settings == {
            "max_tokens": COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS
        }
        openai = run_generation_settings(_resolved(), _runtime(reasoning_effort="high"))
        assert openai.compaction_model_settings == {
            "openai_reasoning_effort": "low",
            "max_tokens": COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS,
        }

    def test_compaction_keeps_an_effort_already_lighter_than_low(self):
        """`minimal` on the run says the model takes it; raising it to `low`
        would make the summary dearer than the run's own turns."""
        settings = run_generation_settings(
            _resolved(), _runtime(reasoning_effort="minimal")
        )

        assert settings.compaction_model_settings["openai_reasoning_effort"] == (
            "minimal"
        )

    def test_compaction_lowers_a_raw_wire_effort_but_never_introduces_one(self):
        """An operator's raw `openai_reasoning_effort` is not a portable field,
        so the REASONING gate never saw it; the summary still turns it down."""
        plain = _resolved(
            capabilities=_PLAIN,
            profile_settings={"openai_reasoning_effort": "high"},
        )

        assert run_generation_settings(plain, None).compaction_model_settings == {
            "openai_reasoning_effort": "low",
            "max_tokens": COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS,
        }
        assert "openai_reasoning_effort" not in (
            run_generation_settings(
                _resolved(capabilities=_PLAIN), None
            ).compaction_model_settings
        )


class TestShorterAnswerRetry:
    """The retry of an over-long answer asks for less thinking through the same
    translation every other request uses: only a model declaring REASONING is
    sent an effort it was not already being sent."""

    def test_a_reasoning_model_is_given_a_light_effort_to_retry_with(self):
        settings = run_generation_settings(_resolved(), None)

        assert settings.model_settings == {}
        assert settings.lighter_reasoning_settings == {"openai_reasoning_effort": "low"}
        retry = shorter_answer_settings(
            {"max_tokens": 4_000},
            lighter_reasoning=settings.lighter_reasoning_settings,
        )
        assert retry == {"max_tokens": 2_000, "openai_reasoning_effort": "low"}

    def test_a_model_that_does_not_reason_is_not(self):
        settings = run_generation_settings(_resolved(capabilities=_PLAIN), None)

        assert settings.lighter_reasoning_settings == {}
        retry = shorter_answer_settings(
            {"max_tokens": 4_000},
            lighter_reasoning=settings.lighter_reasoning_settings,
        )
        assert "openai_reasoning_effort" not in retry

    def test_an_effort_already_light_is_not_raised(self):
        settings = run_generation_settings(
            _resolved(), _runtime(reasoning_effort="minimal")
        )

        retry = shorter_answer_settings(
            settings.model_settings,
            lighter_reasoning=settings.lighter_reasoning_settings,
        )
        assert retry["openai_reasoning_effort"] == "minimal"

    def test_a_thinking_budget_shrinks_with_the_cap(self):
        """Halving the cap alone can leave the budget at or above it, which an
        Anthropic-compatible provider refuses."""
        anthropic = _resolved(protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE)
        settings = run_generation_settings(
            anthropic, _runtime(max_output_tokens=8_000, thinking_budget_tokens=6_000)
        )

        retry = shorter_answer_settings(
            settings.model_settings,
            lighter_reasoning=settings.lighter_reasoning_settings,
        )
        assert retry["max_tokens"] == 4_000
        assert retry["anthropic_thinking"]["budget_tokens"] == 3_000

        small = shorter_answer_settings(
            {
                "max_tokens": 2_048,
                "anthropic_thinking": {"type": "enabled", "budget_tokens": 1_500},
            }
        )
        assert "anthropic_thinking" not in small


def test_the_run_span_records_what_was_asked_and_what_was_sent():
    recorded: dict[str, object] = {}

    class _Span:
        def set_attribute(self, key: str, value: object) -> None:
            recorded[key] = value

    record_generation_settings(
        _Span(),
        GenerationSettings(reasoning_effort=ReasoningEffort.LOW),
        {"max_tokens": 900},
    )

    assert recorded == {
        "lemma.generation.settings": '{"reasoning_effort":"low"}',
        "gen_ai.request.max_tokens": 900,
    }


def test_reasoning_is_declared_exactly_for_the_named_models():
    catalog = [
        RuntimeModelCatalogEntry(name=name, provider_model_name=name, capabilities=caps)
        for name, caps in (("a", _REASONS), ("b", _PLAIN))
    ]

    declared = with_reasoning_declared(catalog, ["b"])

    assert [entry.reasons for entry in declared] == [False, True]
