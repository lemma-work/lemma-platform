from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.field_input import FieldInput

T = TypeVar("T", bound="FormColumnResponse")


@_attrs_define
class FormColumnResponse:
    """
    Attributes:
        description (None | str):
        inputs (list[FieldInput]):
        name (str):
        options (list[str]):
        required (bool):
        suggested_input (FieldInput): What a visitor types into a field. Chosen from the column, adjustable.
        type_ (str):
    """

    description: None | str
    inputs: list[FieldInput]
    name: str
    options: list[str]
    required: bool
    suggested_input: FieldInput
    type_: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description: None | str
        description = self.description

        inputs = []
        for inputs_item_data in self.inputs:
            inputs_item = inputs_item_data.value
            inputs.append(inputs_item)

        name = self.name

        options = self.options

        required = self.required

        suggested_input = self.suggested_input.value

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "inputs": inputs,
                "name": name,
                "options": options,
                "required": required,
                "suggested_input": suggested_input,
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

        inputs = []
        _inputs = d.pop("inputs")
        for inputs_item_data in _inputs:
            inputs_item = FieldInput(inputs_item_data)

            inputs.append(inputs_item)

        name = d.pop("name")

        options = cast(list[str], d.pop("options"))

        required = d.pop("required")

        suggested_input = FieldInput(d.pop("suggested_input"))

        type_ = d.pop("type")

        form_column_response = cls(
            description=description,
            inputs=inputs,
            name=name,
            options=options,
            required=required,
            suggested_input=suggested_input,
            type_=type_,
        )

        form_column_response.additional_properties = d
        return form_column_response

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
