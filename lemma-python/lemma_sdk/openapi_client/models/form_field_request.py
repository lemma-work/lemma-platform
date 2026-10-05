from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.field_input import FieldInput
from ..types import UNSET, Unset

T = TypeVar("T", bound="FormFieldRequest")


@_attrs_define
class FormFieldRequest:
    """
    Attributes:
        column (str):
        hint (None | str | Unset):
        input_ (FieldInput | None | Unset):
        label (None | str | Unset):
        required (bool | Unset):  Default: False.
    """

    column: str
    hint: None | str | Unset = UNSET
    input_: FieldInput | None | Unset = UNSET
    label: None | str | Unset = UNSET
    required: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        hint: None | str | Unset
        if isinstance(self.hint, Unset):
            hint = UNSET
        else:
            hint = self.hint

        input_: None | str | Unset
        if isinstance(self.input_, Unset):
            input_ = UNSET
        elif isinstance(self.input_, FieldInput):
            input_ = self.input_.value
        else:
            input_ = self.input_

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        required = self.required

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
            }
        )
        if hint is not UNSET:
            field_dict["hint"] = hint
        if input_ is not UNSET:
            field_dict["input"] = input_
        if label is not UNSET:
            field_dict["label"] = label
        if required is not UNSET:
            field_dict["required"] = required

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        column = d.pop("column")

        def _parse_hint(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        hint = _parse_hint(d.pop("hint", UNSET))

        def _parse_input_(data: object) -> FieldInput | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                input_type_0 = FieldInput(data)

                return input_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(FieldInput | None | Unset, data)

        input_ = _parse_input_(d.pop("input", UNSET))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        required = d.pop("required", UNSET)

        form_field_request = cls(
            column=column,
            hint=hint,
            input_=input_,
            label=label,
            required=required,
        )

        form_field_request.additional_properties = d
        return form_field_request

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
