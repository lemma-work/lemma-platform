from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="UsageStatsBucketResponse")


@_attrs_define
class UsageStatsBucketResponse:
    """
    Attributes:
        bucket (datetime.datetime):
        input_tokens (int):
        output_tokens (int):
        system_cost_usd (float):
        total_tokens (int):
        units (float):
        cache_write_tokens (int | Unset):  Default: 0.
        cached_input_tokens (int | Unset):  Default: 0.
        group (None | str | Unset):
        total_cost_usd (float | Unset):  Default: 0.0.
        uncached_input_tokens (int | Unset):  Default: 0.
    """

    bucket: datetime.datetime
    input_tokens: int
    output_tokens: int
    system_cost_usd: float
    total_tokens: int
    units: float
    cache_write_tokens: int | Unset = 0
    cached_input_tokens: int | Unset = 0
    group: None | str | Unset = UNSET
    total_cost_usd: float | Unset = 0.0
    uncached_input_tokens: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bucket = self.bucket.isoformat()

        input_tokens = self.input_tokens

        output_tokens = self.output_tokens

        system_cost_usd = self.system_cost_usd

        total_tokens = self.total_tokens

        units = self.units

        cache_write_tokens = self.cache_write_tokens

        cached_input_tokens = self.cached_input_tokens

        group: None | str | Unset
        if isinstance(self.group, Unset):
            group = UNSET
        else:
            group = self.group

        total_cost_usd = self.total_cost_usd

        uncached_input_tokens = self.uncached_input_tokens

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bucket": bucket,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "system_cost_usd": system_cost_usd,
                "total_tokens": total_tokens,
                "units": units,
            }
        )
        if cache_write_tokens is not UNSET:
            field_dict["cache_write_tokens"] = cache_write_tokens
        if cached_input_tokens is not UNSET:
            field_dict["cached_input_tokens"] = cached_input_tokens
        if group is not UNSET:
            field_dict["group"] = group
        if total_cost_usd is not UNSET:
            field_dict["total_cost_usd"] = total_cost_usd
        if uncached_input_tokens is not UNSET:
            field_dict["uncached_input_tokens"] = uncached_input_tokens

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bucket = isoparse(d.pop("bucket"))

        input_tokens = d.pop("input_tokens")

        output_tokens = d.pop("output_tokens")

        system_cost_usd = d.pop("system_cost_usd")

        total_tokens = d.pop("total_tokens")

        units = d.pop("units")

        cache_write_tokens = d.pop("cache_write_tokens", UNSET)

        cached_input_tokens = d.pop("cached_input_tokens", UNSET)

        def _parse_group(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        group = _parse_group(d.pop("group", UNSET))

        total_cost_usd = d.pop("total_cost_usd", UNSET)

        uncached_input_tokens = d.pop("uncached_input_tokens", UNSET)

        usage_stats_bucket_response = cls(
            bucket=bucket,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            system_cost_usd=system_cost_usd,
            total_tokens=total_tokens,
            units=units,
            cache_write_tokens=cache_write_tokens,
            cached_input_tokens=cached_input_tokens,
            group=group,
            total_cost_usd=total_cost_usd,
            uncached_input_tokens=uncached_input_tokens,
        )

        usage_stats_bucket_response.additional_properties = d
        return usage_stats_bucket_response

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
