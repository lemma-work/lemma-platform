from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

T = TypeVar("T", bound="UsageAllowanceResponse")


@_attrs_define
class UsageAllowanceResponse:
    """
    Attributes:
        allowed (bool):
        key (str):
        label (str):
        reset_at (datetime.datetime):
        used_percent (float):
    """

    allowed: bool
    key: str
    label: str
    reset_at: datetime.datetime
    used_percent: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowed = self.allowed

        key = self.key

        label = self.label

        reset_at = self.reset_at.isoformat()

        used_percent = self.used_percent

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowed": allowed,
                "key": key,
                "label": label,
                "reset_at": reset_at,
                "used_percent": used_percent,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        allowed = d.pop("allowed")

        key = d.pop("key")

        label = d.pop("label")

        reset_at = isoparse(d.pop("reset_at"))

        used_percent = d.pop("used_percent")

        usage_allowance_response = cls(
            allowed=allowed,
            key=key,
            label=label,
            reset_at=reset_at,
            used_percent=used_percent,
        )

        usage_allowance_response.additional_properties = d
        return usage_allowance_response

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
