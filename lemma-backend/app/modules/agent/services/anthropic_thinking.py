"""An Anthropic-compatible model that sends a thinking budget in a form it takes.

Some models take only adaptive thinking, and pydantic-ai refuses an
``anthropic_thinking`` budget for them before the request is sent -- which
failed every run of an agent configured with a budget on such a model. The
model's pydantic-ai profile says which models those are
(``anthropic_disallows_budget_thinking``); nothing here names a model.

The budget becomes the nearest unified ``thinking`` level, which pydantic-ai
sends to such a model as adaptive thinking, with the matching effort where the
profile says the model takes one.

Imported only where an Anthropic-compatible model is built: it brings the
provider SDK with it.
"""

from __future__ import annotations

from typing import cast

from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.profiles.anthropic import (
    ANTHROPIC_THINKING_BUDGET_MAP,
    AnthropicModelProfile,
)
from pydantic_ai.settings import ModelSettings, ThinkingEffort, merge_model_settings

__all__ = [
    "AdaptiveThinkingAnthropicModel",
    "adaptive_in_place_of_budget",
    "nearest_thinking_effort",
]


def nearest_thinking_effort(budget_tokens: int) -> ThinkingEffort:
    """The unified thinking level whose budget is closest to ``budget_tokens``.

    Read off pydantic-ai's own level-to-budget table, so a budget means here
    what the same level means on a model that still takes budgets. A tie goes
    to the lighter level.
    """
    levels: list[tuple[int, ThinkingEffort]] = [
        (budget, level)
        for level, budget in ANTHROPIC_THINKING_BUDGET_MAP.items()
        if isinstance(level, str)
    ]
    _, nearest = min(
        levels, key=lambda entry: (abs(entry[0] - budget_tokens), entry[0])
    )
    return nearest


def adaptive_in_place_of_budget(
    profile: AnthropicModelProfile, settings: ModelSettings | None
) -> ModelSettings | None:
    """``settings`` with a thinking budget restated for a model that refuses one.

    Unchanged for a model that takes budgets, and for settings that name no
    budget. A ``thinking`` level the request already names is kept.
    """
    if not settings or not profile.get("anthropic_disallows_budget_thinking", False):
        return settings
    anthropic = cast(AnthropicModelSettings, settings)
    thinking = anthropic.get("anthropic_thinking")
    if not thinking or thinking.get("type") != "enabled":
        return settings
    translated = cast(AnthropicModelSettings, {**anthropic})
    del translated["anthropic_thinking"]
    if "thinking" not in translated:
        budget = thinking.get("budget_tokens")
        translated["thinking"] = (
            nearest_thinking_effort(budget) if isinstance(budget, int) else True
        )
    return translated


class AdaptiveThinkingAnthropicModel(AnthropicModel):
    """``AnthropicModel``, with a budget restated where the profile refuses one.

    Translated here, as the request is prepared, because only the model knows
    its profile: a run's settings are resolved from the catalog and the agent,
    which name a budget and not the form a given model accepts.
    """

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings | None, ModelRequestParameters]:
        merged = merge_model_settings(self.settings, model_settings)
        return super().prepare_request(
            adaptive_in_place_of_budget(
                cast(AnthropicModelProfile, self.profile), merged
            ),
            model_request_parameters,
        )
