from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="UsageLimitScopeResponse")


@_attrs_define
class UsageLimitScopeResponse:
    """
    Attributes:
        allowed (bool):
        reserved_usd (float):
        reset_at (datetime.datetime):
        scope (str):
        used_usd (float):
        window_start (datetime.datetime):
        limit_usd (float | None | Unset):
        remaining_usd (float | None | Unset):
    """

    allowed: bool
    reserved_usd: float
    reset_at: datetime.datetime
    scope: str
    used_usd: float
    window_start: datetime.datetime
    limit_usd: float | None | Unset = UNSET
    remaining_usd: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowed = self.allowed

        reserved_usd = self.reserved_usd

        reset_at = self.reset_at.isoformat()

        scope = self.scope

        used_usd = self.used_usd

        window_start = self.window_start.isoformat()

        limit_usd: float | None | Unset
        if isinstance(self.limit_usd, Unset):
            limit_usd = UNSET
        else:
            limit_usd = self.limit_usd

        remaining_usd: float | None | Unset
        if isinstance(self.remaining_usd, Unset):
            remaining_usd = UNSET
        else:
            remaining_usd = self.remaining_usd

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowed": allowed,
                "reserved_usd": reserved_usd,
                "reset_at": reset_at,
                "scope": scope,
                "used_usd": used_usd,
                "window_start": window_start,
            }
        )
        if limit_usd is not UNSET:
            field_dict["limit_usd"] = limit_usd
        if remaining_usd is not UNSET:
            field_dict["remaining_usd"] = remaining_usd

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        allowed = d.pop("allowed")

        reserved_usd = d.pop("reserved_usd")

        reset_at = isoparse(d.pop("reset_at"))

        scope = d.pop("scope")

        used_usd = d.pop("used_usd")

        window_start = isoparse(d.pop("window_start"))

        def _parse_limit_usd(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        limit_usd = _parse_limit_usd(d.pop("limit_usd", UNSET))

        def _parse_remaining_usd(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        remaining_usd = _parse_remaining_usd(d.pop("remaining_usd", UNSET))

        usage_limit_scope_response = cls(
            allowed=allowed,
            reserved_usd=reserved_usd,
            reset_at=reset_at,
            scope=scope,
            used_usd=used_usd,
            window_start=window_start,
            limit_usd=limit_usd,
            remaining_usd=remaining_usd,
        )

        usage_limit_scope_response.additional_properties = d
        return usage_limit_scope_response

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
