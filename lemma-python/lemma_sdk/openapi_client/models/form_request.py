from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.form_field_request import FormFieldRequest


T = TypeVar("T", bound="FormRequest")


@_attrs_define
class FormRequest:
    """A form on one table: the columns to ask for, in order.

    Attributes:
        fields (list[FormFieldRequest]):
        table (str):
        confirmation (None | str | Unset):
        intro (None | str | Unset):
    """

    fields: list[FormFieldRequest]
    table: str
    confirmation: None | str | Unset = UNSET
    intro: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        fields = []
        for fields_item_data in self.fields:
            fields_item = fields_item_data.to_dict()
            fields.append(fields_item)

        table = self.table

        confirmation: None | str | Unset
        if isinstance(self.confirmation, Unset):
            confirmation = UNSET
        else:
            confirmation = self.confirmation

        intro: None | str | Unset
        if isinstance(self.intro, Unset):
            intro = UNSET
        else:
            intro = self.intro

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "fields": fields,
                "table": table,
            }
        )
        if confirmation is not UNSET:
            field_dict["confirmation"] = confirmation
        if intro is not UNSET:
            field_dict["intro"] = intro

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.form_field_request import FormFieldRequest

        d = dict(src_dict)
        fields = []
        _fields = d.pop("fields")
        for fields_item_data in _fields:
            fields_item = FormFieldRequest.from_dict(fields_item_data)

            fields.append(fields_item)

        table = d.pop("table")

        def _parse_confirmation(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        confirmation = _parse_confirmation(d.pop("confirmation", UNSET))

        def _parse_intro(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        intro = _parse_intro(d.pop("intro", UNSET))

        form_request = cls(
            fields=fields,
            table=table,
            confirmation=confirmation,
            intro=intro,
        )

        form_request.additional_properties = d
        return form_request

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
