"""How a model generates, in words that do not name a provider.

Shared by the agent module (runs, runtime profiles) and the workflow module
(agent steps), so it lives in core beside ``AgentRuntimeConfig``. Translating
these fields into a provider's request parameters is the agent module's job,
in one place, keyed by wire protocol rather than by vendor.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


class ReasoningEffort(StrEnum):
    """How long a reasoning model thinks before it answers.

    The values are the OpenAI Chat Completions ``reasoning_effort`` vocabulary,
    which is the wire format OPENAI_COMPATIBLE speaks. Which values a given
    model accepts is the model's business; ``none`` and ``minimal`` in
    particular are not universal.
    """

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


#: Efforts already as light as a cheap call -- a history summary, the shorter
#: retry of an answer that ran too long -- would ask for. ``low`` is the
#: lightest every reasoning model accepts; ``none`` and ``minimal`` are kept
#: only where the run already sends them, which says the model takes them.
LIGHT_REASONING_EFFORTS = frozenset(
    {ReasoningEffort.NONE, ReasoningEffort.MINIMAL, ReasoningEffort.LOW}
)


def lighter_effort(effort: ReasoningEffort | str | None) -> ReasoningEffort:
    """``effort`` turned down for a cheap call: kept when already light, else low."""
    if effort in LIGHT_REASONING_EFFORTS:
        return ReasoningEffort(effort)
    return ReasoningEffort.LOW


class GenerationSettings(BaseModel):
    """Portable generation settings. Every field unset means "provider default".

    Layered lowest to highest: the model's catalog entry, the runtime profile,
    the agent, the workflow step. A field a layer leaves unset is inherited
    from the layer below, so an agent that only lowers reasoning effort keeps
    whatever output cap its profile chose.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Most tokens one model response may use, thinking included. Still "
            "clamped to the room the context window leaves."
        ),
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description=(
            "Sent only to OpenAI-compatible models whose catalog entry declares "
            "the REASONING capability; ignored elsewhere."
        ),
    )
    thinking_budget_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Extended-thinking budget. Sent only to Anthropic-compatible models "
            "whose catalog entry declares the REASONING capability; ignored "
            "elsewhere."
        ),
    )

    @model_validator(mode="after")
    def _thinking_fits_the_response(self) -> GenerationSettings:
        # Thinking tokens are output tokens: a budget at or above the cap leaves
        # nothing for the answer, and the provider refuses the request.
        if (
            self.thinking_budget_tokens is not None
            and self.max_output_tokens is not None
            and self.thinking_budget_tokens >= self.max_output_tokens
        ):
            raise ValueError(
                "thinking_budget_tokens must be smaller than max_output_tokens"
            )
        return self

    @model_serializer(mode="wrap")
    def _serialize_set_fields(self, handler):
        # Stored inside agent, conversation and run rows: an unset field is
        # "inherit", and writing it out as null would only add noise there.
        data = handler(self)
        return {key: value for key, value in data.items() if value is not None}

    def is_empty(self) -> bool:
        return not self.model_dump()

    def overlaid_with(self, override: GenerationSettings | None) -> GenerationSettings:
        """These settings, with every field ``override`` sets taking its place."""
        if override is None:
            return self
        return self.model_copy(update=override.model_dump(exclude_none=True))


__all__ = [
    "LIGHT_REASONING_EFFORTS",
    "GenerationSettings",
    "ReasoningEffort",
    "lighter_effort",
]
