from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.reasoning_effort import ReasoningEffort
from ..types import UNSET, Unset

T = TypeVar("T", bound="GenerationSettings")


@_attrs_define
class GenerationSettings:
    """Portable generation settings. Every field unset means "provider default".

    Layered lowest to highest: the model's catalog entry, the runtime profile,
    the agent, the workflow step. A field a layer leaves unset is inherited
    from the layer below, so an agent that only lowers reasoning effort keeps
    whatever output cap its profile chose.

        Attributes:
            max_output_tokens (int | None | Unset): Most tokens one model response may use, thinking included. Still clamped
                to the room the context window leaves.
            reasoning_effort (None | ReasoningEffort | Unset): Sent only to OpenAI-compatible models whose catalog entry
                declares the REASONING capability; ignored elsewhere.
            temperature (float | None | Unset):
            thinking_budget_tokens (int | None | Unset): Extended-thinking budget. Sent only to Anthropic-compatible models
                whose catalog entry declares the REASONING capability; ignored elsewhere.
            top_p (float | None | Unset):
    """

    max_output_tokens: int | None | Unset = UNSET
    reasoning_effort: None | ReasoningEffort | Unset = UNSET
    temperature: float | None | Unset = UNSET
    thinking_budget_tokens: int | None | Unset = UNSET
    top_p: float | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        max_output_tokens: int | None | Unset
        if isinstance(self.max_output_tokens, Unset):
            max_output_tokens = UNSET
        else:
            max_output_tokens = self.max_output_tokens

        reasoning_effort: None | str | Unset
        if isinstance(self.reasoning_effort, Unset):
            reasoning_effort = UNSET
        elif isinstance(self.reasoning_effort, ReasoningEffort):
            reasoning_effort = self.reasoning_effort.value
        else:
            reasoning_effort = self.reasoning_effort

        temperature: float | None | Unset
        if isinstance(self.temperature, Unset):
            temperature = UNSET
        else:
            temperature = self.temperature

        thinking_budget_tokens: int | None | Unset
        if isinstance(self.thinking_budget_tokens, Unset):
            thinking_budget_tokens = UNSET
        else:
            thinking_budget_tokens = self.thinking_budget_tokens

        top_p: float | None | Unset
        if isinstance(self.top_p, Unset):
            top_p = UNSET
        else:
            top_p = self.top_p

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if max_output_tokens is not UNSET:
            field_dict["max_output_tokens"] = max_output_tokens
        if reasoning_effort is not UNSET:
            field_dict["reasoning_effort"] = reasoning_effort
        if temperature is not UNSET:
            field_dict["temperature"] = temperature
        if thinking_budget_tokens is not UNSET:
            field_dict["thinking_budget_tokens"] = thinking_budget_tokens
        if top_p is not UNSET:
            field_dict["top_p"] = top_p

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_max_output_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_output_tokens = _parse_max_output_tokens(d.pop("max_output_tokens", UNSET))

        def _parse_reasoning_effort(data: object) -> None | ReasoningEffort | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                reasoning_effort_type_0 = ReasoningEffort(data)

                return reasoning_effort_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ReasoningEffort | Unset, data)

        reasoning_effort = _parse_reasoning_effort(d.pop("reasoning_effort", UNSET))

        def _parse_temperature(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        temperature = _parse_temperature(d.pop("temperature", UNSET))

        def _parse_thinking_budget_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        thinking_budget_tokens = _parse_thinking_budget_tokens(
            d.pop("thinking_budget_tokens", UNSET)
        )

        def _parse_top_p(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        top_p = _parse_top_p(d.pop("top_p", UNSET))

        generation_settings = cls(
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            thinking_budget_tokens=thinking_budget_tokens,
            top_p=top_p,
        )

        return generation_settings
