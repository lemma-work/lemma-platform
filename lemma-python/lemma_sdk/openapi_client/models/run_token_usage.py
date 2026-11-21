from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RunTokenUsage")


@_attrs_define
class RunTokenUsage:
    """Model tokens one run used, summed over its model calls.

    Attributes:
        cache_write_tokens (int | Unset): Input tokens written to the provider's prompt cache. Default: 0.
        cached_input_tokens (int | Unset): Input tokens read from the provider's prompt cache. Default: 0.
        input_tokens (int | Unset): Tokens sent to the model, cached ones included (cached_input_tokens says how many of
            these were cache reads). Default: 0.
        models (list[str] | Unset): Every model the run's calls were recorded against, by name.
        output_tokens (int | Unset): Tokens the model wrote. Default: 0.
    """

    cache_write_tokens: int | Unset = 0
    cached_input_tokens: int | Unset = 0
    input_tokens: int | Unset = 0
    models: list[str] | Unset = UNSET
    output_tokens: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cache_write_tokens = self.cache_write_tokens

        cached_input_tokens = self.cached_input_tokens

        input_tokens = self.input_tokens

        models: list[str] | Unset = UNSET
        if not isinstance(self.models, Unset):
            models = self.models

        output_tokens = self.output_tokens

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if cache_write_tokens is not UNSET:
            field_dict["cache_write_tokens"] = cache_write_tokens
        if cached_input_tokens is not UNSET:
            field_dict["cached_input_tokens"] = cached_input_tokens
        if input_tokens is not UNSET:
            field_dict["input_tokens"] = input_tokens
        if models is not UNSET:
            field_dict["models"] = models
        if output_tokens is not UNSET:
            field_dict["output_tokens"] = output_tokens

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cache_write_tokens = d.pop("cache_write_tokens", UNSET)

        cached_input_tokens = d.pop("cached_input_tokens", UNSET)

        input_tokens = d.pop("input_tokens", UNSET)

        models = cast(list[str], d.pop("models", UNSET))

        output_tokens = d.pop("output_tokens", UNSET)

        run_token_usage = cls(
            cache_write_tokens=cache_write_tokens,
            cached_input_tokens=cached_input_tokens,
            input_tokens=input_tokens,
            models=models,
            output_tokens=output_tokens,
        )

        run_token_usage.additional_properties = d
        return run_token_usage

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
