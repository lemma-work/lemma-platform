from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="LedgerTotals")


@_attrs_define
class LedgerTotals:
    """What a set of ledger rows cost, and the model tokens among them.

    Attributes:
        cache_write_tokens (int | Unset): Input tokens written to the provider's prompt cache. Default: 0.
        cached_input_tokens (int | Unset): Input tokens read from the provider's prompt cache. Default: 0.
        cost_usd (float | Unset): What every recorded call cost, in US dollars, whichever provider profile paid for it.
            A call recorded without a price adds nothing. Default: 0.0.
        input_tokens (int | Unset): Tokens sent to the model, cached ones included (cached_input_tokens says how many of
            these were cache reads). Default: 0.
        model_calls (int | Unset): Model calls recorded: one per request for a run Lemma answers itself; an Agent Host
            reports each turn's calls as one. Default: 0.
        output_tokens (int | Unset): Tokens the model wrote. Default: 0.
    """

    cache_write_tokens: int | Unset = 0
    cached_input_tokens: int | Unset = 0
    cost_usd: float | Unset = 0.0
    input_tokens: int | Unset = 0
    model_calls: int | Unset = 0
    output_tokens: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cache_write_tokens = self.cache_write_tokens

        cached_input_tokens = self.cached_input_tokens

        cost_usd = self.cost_usd

        input_tokens = self.input_tokens

        model_calls = self.model_calls

        output_tokens = self.output_tokens

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if cache_write_tokens is not UNSET:
            field_dict["cache_write_tokens"] = cache_write_tokens
        if cached_input_tokens is not UNSET:
            field_dict["cached_input_tokens"] = cached_input_tokens
        if cost_usd is not UNSET:
            field_dict["cost_usd"] = cost_usd
        if input_tokens is not UNSET:
            field_dict["input_tokens"] = input_tokens
        if model_calls is not UNSET:
            field_dict["model_calls"] = model_calls
        if output_tokens is not UNSET:
            field_dict["output_tokens"] = output_tokens

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cache_write_tokens = d.pop("cache_write_tokens", UNSET)

        cached_input_tokens = d.pop("cached_input_tokens", UNSET)

        cost_usd = d.pop("cost_usd", UNSET)

        input_tokens = d.pop("input_tokens", UNSET)

        model_calls = d.pop("model_calls", UNSET)

        output_tokens = d.pop("output_tokens", UNSET)

        ledger_totals = cls(
            cache_write_tokens=cache_write_tokens,
            cached_input_tokens=cached_input_tokens,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            model_calls=model_calls,
            output_tokens=output_tokens,
        )

        ledger_totals.additional_properties = d
        return ledger_totals

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
