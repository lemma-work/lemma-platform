"""Where a run's generation settings come from, and what goes on the wire.

Four layers, lowest to highest: the model's catalog entry
(``default_model_settings``), the runtime profile (``config.model_settings``),
the agent, and the workflow step that started the run. The last two arrive
already merged on the run's ``AgentRuntimeConfig.settings``.

The two stored layers are JSON written by an operator, and hold two kinds of
key. The portable ``GenerationSettings`` fields are translated here, by wire
protocol. Anything else is an operator's wire switch in pydantic-ai's own
vocabulary (``openai_continuous_usage_stats``, a legacy ``max_tokens``) and is
passed through as written -- it was always sent, and stops being sent only when
the operator removes it.

Translation is generic: OPENAI_COMPATIBLE is baseline Chat Completions, so
``reasoning_effort`` is sent only to a model whose catalog entry declares
``REASONING``; a model that does not reason rejects the field. The same gate
holds for an Anthropic-compatible thinking budget.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from opentelemetry.util.types import AttributeValue
from pydantic import ValidationError

from app.core.domain.generation import (
    GenerationSettings,
    ReasoningEffort,
    lighter_effort,
)
from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeProfileProtocol,
    describe_invalid,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig, JsonObject
from app.modules.agent.services.run_input_settings import (
    COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS,
    with_light_reasoning,
)
from app.modules.agent.services.runtime_resolution import ResolvedAgentRuntime

PORTABLE_KEYS = frozenset(GenerationSettings.model_fields)


@dataclass(frozen=True, slots=True)
class SettingsLayer:
    """One stored layer, split into portable settings and raw wire switches."""

    generation: GenerationSettings
    wire: JsonObject

    @classmethod
    def of(cls, generation: GenerationSettings | None) -> SettingsLayer:
        return cls(generation=generation or GenerationSettings(), wire={})


def split_stored_settings(stored: Mapping[str, object] | None) -> SettingsLayer:
    """Read a stored ``model_settings`` / ``default_model_settings`` object.

    Raises ``ValueError`` (a pydantic ``ValidationError``) when a portable field
    holds a value it cannot.
    """
    portable: JsonObject = {}
    wire: JsonObject = {}
    for key, value in (stored or {}).items():
        if key in PORTABLE_KEYS:
            if value is not None:
                portable[key] = value
        else:
            wire[key] = value
    return SettingsLayer(
        generation=GenerationSettings.model_validate(portable), wire=wire
    )


def validated_stored_settings(stored: Mapping[str, object] | None) -> JsonObject:
    """``stored``, refused on save if a run could not use it.

    The same two checks a run applies, moved to the edit: a profile saved with
    ``temperature: "hot"`` used to be accepted and then fail every run on it.
    """
    # Imported here: this module is on the API's import path (profile edits),
    # and the factory brings the provider SDKs with it.
    from app.modules.agent.services.runtime_model_factory import (
        provider_model_settings,
    )

    try:
        provider_model_settings(split_stored_settings(stored).wire)
    except ValidationError as exc:
        # Described, not quoted: pydantic's own text repeats the input.
        raise RuntimeProfileValidationError(
            describe_invalid(exc, prefix="model_settings")
        ) from None
    return dict(stored or {})


def wire_settings(
    generation: GenerationSettings,
    *,
    protocol: RuntimeProfileProtocol | str,
    capabilities: Collection[RuntimeModelCapability | str],
) -> JsonObject:
    """``generation`` as the request parameters ``protocol`` takes.

    Empty for anything that is not a model provider: an Agent Host harness
    runs its own model loop and is given none of these.
    """
    protocol_value = RuntimeProfileProtocol(protocol)
    if protocol_value not in (
        RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
    ):
        return {}
    reasoning = RuntimeModelCapability.REASONING.value in {
        RuntimeModelCapability(capability).value for capability in capabilities
    }
    wire: JsonObject = {}
    if generation.max_output_tokens is not None:
        wire["max_tokens"] = generation.max_output_tokens
    if generation.temperature is not None:
        wire["temperature"] = generation.temperature
    if generation.top_p is not None:
        wire["top_p"] = generation.top_p
    if not reasoning:
        return wire
    if (
        protocol_value is RuntimeProfileProtocol.OPENAI_COMPATIBLE
        and generation.reasoning_effort is not None
    ):
        wire["openai_reasoning_effort"] = generation.reasoning_effort.value
    if (
        protocol_value is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE
        and generation.thinking_budget_tokens is not None
    ):
        wire["anthropic_thinking"] = {
            "type": "enabled",
            "budget_tokens": generation.thinking_budget_tokens,
        }
    return wire


def merged_wire_settings(
    layers: Sequence[SettingsLayer],
    *,
    protocol: RuntimeProfileProtocol | str,
    capabilities: Collection[RuntimeModelCapability | str],
) -> JsonObject:
    """Every layer's wire parameters, a higher layer replacing a lower one.

    Merged on the wire keys rather than on the portable fields, so a profile's
    legacy ``max_tokens`` and an agent's ``max_output_tokens`` -- two spellings
    of one parameter -- still resolve by layer.
    """
    wire: JsonObject = {}
    for layer in layers:
        wire.update(layer.wire)
        wire.update(
            wire_settings(
                layer.generation, protocol=protocol, capabilities=capabilities
            )
        )
    return wire


@dataclass(frozen=True, slots=True)
class RunGenerationSettings:
    """What one run generates with, before the context budget clamps it."""

    #: The portable settings every layer added up to; what the run reports.
    effective: GenerationSettings
    #: Request parameters for the run's model calls.
    model_settings: JsonObject
    #: Request parameters for the run's history-compaction call.
    compaction_model_settings: JsonObject
    #: A light reasoning effort, as this model takes one: empty unless its
    #: catalog entry declares REASONING. What the shorter retry of an over-long
    #: answer lays over the run's settings (``shorter_answer_settings``).
    lighter_reasoning_settings: JsonObject


def run_generation_settings(
    resolved: ResolvedAgentRuntime, runtime: AgentRuntimeConfig | None
) -> RunGenerationSettings:
    """The four layers for this run's resolved model, merged."""
    layers = [
        split_stored_settings(
            resolved.model.default_model_settings if resolved.model else None
        ),
        split_stored_settings(_profile_model_settings(resolved)),
        SettingsLayer.of(runtime.settings if runtime else None),
    ]
    effective = GenerationSettings()
    for layer in layers:
        effective = effective.overlaid_with(layer.generation)
    protocol = resolved.profile.protocol
    capabilities = resolved.capabilities
    model_settings = merged_wire_settings(
        layers, protocol=protocol, capabilities=capabilities
    )
    lighter_reasoning_settings = wire_settings(
        GenerationSettings(reasoning_effort=lighter_effort(effective.reasoning_effort)),
        protocol=protocol,
        capabilities=capabilities,
    )
    # An effort the portable settings did not name -- an operator's raw wire
    # switch -- is turned down too, though never introduced on a model that
    # does not declare it reasons.
    compaction_model_settings = with_light_reasoning(
        {
            **model_settings,
            **wire_settings(
                compaction_generation_settings(effective),
                protocol=protocol,
                capabilities=capabilities,
            ),
        },
        lighter_reasoning_settings,
    )
    # The run's thinking budget does not carry into a summary: no thinking is
    # what a summary wants, and leaving the key out is how that is said.
    compaction_model_settings.pop("anthropic_thinking", None)
    return RunGenerationSettings(
        effective=effective,
        model_settings=model_settings,
        compaction_model_settings=compaction_model_settings,
        lighter_reasoning_settings=lighter_reasoning_settings,
    )


def compaction_generation_settings(merged: GenerationSettings) -> GenerationSettings:
    """The run's settings, turned down for writing a history summary.

    Low effort rather than ``none``: ``none`` is not a value every reasoning
    model accepts, and ``low`` is. An effort the run already keeps lighter than
    that stays as it is. The output cap is the summary's own, or the run's when
    that is smaller.
    """
    summary_cap = COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS
    if merged.max_output_tokens is not None:
        summary_cap = min(summary_cap, merged.max_output_tokens)
    return merged.model_copy(
        update={
            "reasoning_effort": lighter_effort(merged.reasoning_effort),
            "thinking_budget_tokens": None,
            "max_output_tokens": summary_cap,
        }
    )


def unreasoned_model_settings(
    resolved: ResolvedAgentRuntime, *, ask_no_reasoning: bool = True
) -> JsonObject:
    """Request parameters for a small helper call: no reasoning at all.

    A conversation title is a few words, and a memory pass mostly decides
    there is nothing to keep; a reasoning model left at its default spends
    almost all of its output on thinking before either. ``none`` is asked of
    every model, declared reasoning or not -- an undeclared reasoning model is
    exactly the one that would otherwise think at length. A model that rejects
    the field is asked again without it (``ask_no_reasoning=False``); see the
    title generator and the memory pass.
    """
    capabilities = list(resolved.capabilities)
    if ask_no_reasoning:
        capabilities.append(RuntimeModelCapability.REASONING)
    else:
        capabilities = [
            c
            for c in capabilities
            if RuntimeModelCapability(c) is not RuntimeModelCapability.REASONING
        ]
    return wire_settings(
        GenerationSettings(reasoning_effort=ReasoningEffort.NONE),
        protocol=resolved.profile.protocol,
        capabilities=capabilities,
    )


class _AttributeSink(Protocol):
    def set_attribute(self, key: str, value: AttributeValue) -> object: ...


def record_generation_settings(
    span: _AttributeSink,
    effective: GenerationSettings,
    model_settings: Mapping[str, object] | None,
) -> None:
    """Put on the run's span what it asked for and what it actually sent.

    Both, because they differ on purpose: an effort the model cannot take is
    dropped, and an output cap is clamped to the window.
    """
    if not effective.is_empty():
        span.set_attribute("lemma.generation.settings", effective.model_dump_json())
    sent = model_settings or {}
    for wire_key, attribute in _SENT_ATTRIBUTES:
        value = sent.get(wire_key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            span.set_attribute(attribute, value)
    thinking = sent.get("anthropic_thinking")
    if isinstance(thinking, Mapping):
        budget = thinking.get("budget_tokens")
        if isinstance(budget, int):
            span.set_attribute("lemma.generation.thinking_budget_tokens", budget)


_SENT_ATTRIBUTES = (
    ("max_tokens", "gen_ai.request.max_tokens"),
    ("temperature", "gen_ai.request.temperature"),
    ("top_p", "gen_ai.request.top_p"),
    ("openai_reasoning_effort", "lemma.generation.reasoning_effort"),
)


def _profile_model_settings(resolved: ResolvedAgentRuntime) -> Mapping[str, object]:
    stored = getattr(resolved.profile.config, "model_settings", None)
    if isinstance(stored, Mapping):
        return stored
    if isinstance(resolved.profile.config, Mapping):
        nested = resolved.profile.config.get("model_settings")
        if isinstance(nested, Mapping):
            return nested
    return {}


__all__ = [
    "COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS",
    "RunGenerationSettings",
    "SettingsLayer",
    "compaction_generation_settings",
    "merged_wire_settings",
    "record_generation_settings",
    "run_generation_settings",
    "split_stored_settings",
    "unreasoned_model_settings",
    "validated_stored_settings",
    "wire_settings",
]
