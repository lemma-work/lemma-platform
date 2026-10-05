from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.form_column_response import FormColumnResponse


T = TypeVar("T", bound="FormColumnsResponse")


@_attrs_define
class FormColumnsResponse:
    """
    Attributes:
        columns (list[FormColumnResponse]):
        contact_owned (bool):
        table (str):
    """

    columns: list[FormColumnResponse]
    contact_owned: bool
    table: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        columns = []
        for columns_item_data in self.columns:
            columns_item = columns_item_data.to_dict()
            columns.append(columns_item)

        contact_owned = self.contact_owned

        table = self.table

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "columns": columns,
                "contact_owned": contact_owned,
                "table": table,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.form_column_response import FormColumnResponse

        d = dict(src_dict)
        columns = []
        _columns = d.pop("columns")
        for columns_item_data in _columns:
            columns_item = FormColumnResponse.from_dict(columns_item_data)

            columns.append(columns_item)

        contact_owned = d.pop("contact_owned")

        table = d.pop("table")

        form_columns_response = cls(
            columns=columns,
            contact_owned=contact_owned,
            table=table,
        )

        form_columns_response.additional_properties = d
        return form_columns_response

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
