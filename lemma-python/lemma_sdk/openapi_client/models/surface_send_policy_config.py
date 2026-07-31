from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceSendPolicyConfig")


@_attrs_define
class SurfaceSendPolicyConfig:
    """Proactive-send controls. Mirrored across request and response.

    Attributes:
        allow_send (bool | Unset):  Default: False.
    """

    allow_send: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allow_send = self.allow_send

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if allow_send is not UNSET:
            field_dict["allow_send"] = allow_send

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        allow_send = d.pop("allow_send", UNSET)

        surface_send_policy_config = cls(
            allow_send=allow_send,
        )

        surface_send_policy_config.additional_properties = d
        return surface_send_policy_config

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
