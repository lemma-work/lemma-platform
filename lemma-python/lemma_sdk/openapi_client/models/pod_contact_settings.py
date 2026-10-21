from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PodContactSettings")


@_attrs_define
class PodContactSettings:
    """How the pod treats the people outside it who write to it.

    Attributes:
        auto_acknowledge (None | str | Unset): Said once to a contact who writes while their channel's agents are off --
            for example "Thanks, someone from {pod} will reply" -- until a member writes to them or their channel is closed.
            ``{pod}`` is the pod's name. Omitted means nothing is said.
    """

    auto_acknowledge: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auto_acknowledge: None | str | Unset
        if isinstance(self.auto_acknowledge, Unset):
            auto_acknowledge = UNSET
        else:
            auto_acknowledge = self.auto_acknowledge

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if auto_acknowledge is not UNSET:
            field_dict["auto_acknowledge"] = auto_acknowledge

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_auto_acknowledge(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        auto_acknowledge = _parse_auto_acknowledge(d.pop("auto_acknowledge", UNSET))

        pod_contact_settings = cls(
            auto_acknowledge=auto_acknowledge,
        )

        pod_contact_settings.additional_properties = d
        return pod_contact_settings

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
