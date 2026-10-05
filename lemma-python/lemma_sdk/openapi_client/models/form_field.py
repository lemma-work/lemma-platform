from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.field_input import FieldInput
from ..types import UNSET, Unset

T = TypeVar("T", bound="FormField")


@_attrs_define
class FormField:
    """
    Attributes:
        column (str):
        column_type (str):
        input_ (FieldInput): What a visitor types into a field. Chosen from the column, adjustable.
        label (str):
        hint (None | str | Unset):
        options (list[str] | Unset):
        required (bool | Unset):  Default: False.
    """

    column: str
    column_type: str
    input_: FieldInput
    label: str
    hint: None | str | Unset = UNSET
    options: list[str] | Unset = UNSET
    required: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        column_type = self.column_type

        input_ = self.input_.value

        label = self.label

        hint: None | str | Unset
        if isinstance(self.hint, Unset):
            hint = UNSET
        else:
            hint = self.hint

        options: list[str] | Unset = UNSET
        if not isinstance(self.options, Unset):
            options = self.options

        required = self.required

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
                "column_type": column_type,
                "input": input_,
                "label": label,
            }
        )
        if hint is not UNSET:
            field_dict["hint"] = hint
        if options is not UNSET:
            field_dict["options"] = options
        if required is not UNSET:
            field_dict["required"] = required

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        column = d.pop("column")

        column_type = d.pop("column_type")

        input_ = FieldInput(d.pop("input"))

        label = d.pop("label")

        def _parse_hint(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        hint = _parse_hint(d.pop("hint", UNSET))

        options = cast(list[str], d.pop("options", UNSET))

        required = d.pop("required", UNSET)

        form_field = cls(
            column=column,
            column_type=column_type,
            input_=input_,
            label=label,
            hint=hint,
            options=options,
            required=required,
        )

        form_field.additional_properties = d
        return form_field

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
