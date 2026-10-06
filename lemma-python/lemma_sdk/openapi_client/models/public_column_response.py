from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PublicColumnResponse")


@_attrs_define
class PublicColumnResponse:
    """
    Attributes:
        description (None | str):
        name (str):
        options (list[str]):
        required (bool): The table needs it, so it must be open.
        type_ (str):
    """

    description: None | str
    name: str
    options: list[str]
    required: bool
    type_: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description: None | str
        description = self.description

        name = self.name

        options = self.options

        required = self.required

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "name": name,
                "options": options,
                "required": required,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_description(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        description = _parse_description(d.pop("description"))

        name = d.pop("name")

        options = cast(list[str], d.pop("options"))

        required = d.pop("required")

        type_ = d.pop("type")

        public_column_response = cls(
            description=description,
            name=name,
            options=options,
            required=required,
            type_=type_,
        )

        public_column_response.additional_properties = d
        return public_column_response

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
