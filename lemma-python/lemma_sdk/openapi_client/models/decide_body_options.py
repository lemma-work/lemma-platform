from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.decide_body_options_additional_property import (
        DecideBodyOptionsAdditionalProperty,
    )


T = TypeVar("T", bound="DecideBodyOptions")


@_attrs_define
class DecideBodyOptions:
    """Per question, options to add to the declared ones for this call: the person's pods, the open conversations, a list
    built just now.

    """

    additional_properties: dict[str, DecideBodyOptionsAdditionalProperty] = (
        _attrs_field(init=False, factory=dict)
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.to_dict()

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decide_body_options_additional_property import (
            DecideBodyOptionsAdditionalProperty,
        )

        d = dict(src_dict)
        decide_body_options = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = DecideBodyOptionsAdditionalProperty.from_dict(
                prop_dict
            )

            additional_properties[prop_name] = additional_property

        decide_body_options.additional_properties = additional_properties
        return decide_body_options

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> DecideBodyOptionsAdditionalProperty:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: DecideBodyOptionsAdditionalProperty) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
