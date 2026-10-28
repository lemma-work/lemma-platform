from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.call_config_response_engines_item import CallConfigResponseEnginesItem

T = TypeVar("T", bound="CallConfigResponse")


@_attrs_define
class CallConfigResponse:
    """
    Attributes:
        engines (list[CallConfigResponseEnginesItem]): The voice engines this server can open, the default first.
    """

    engines: list[CallConfigResponseEnginesItem]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        engines = []
        for engines_item_data in self.engines:
            engines_item = engines_item_data.value
            engines.append(engines_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "engines": engines,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        engines = []
        _engines = d.pop("engines")
        for engines_item_data in _engines:
            engines_item = CallConfigResponseEnginesItem(engines_item_data)

            engines.append(engines_item)

        call_config_response = cls(
            engines=engines,
        )

        call_config_response.additional_properties = d
        return call_config_response

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
