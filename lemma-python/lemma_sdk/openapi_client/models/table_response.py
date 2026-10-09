from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.table_column import TableColumn


T = TypeVar("T", bound="TableResponse")


@_attrs_define
class TableResponse:
    """
    Attributes:
        columns (list[TableColumn]):
        contacts_only (bool):
        table (str):
    """

    columns: list[TableColumn]
    contacts_only: bool
    table: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        columns = []
        for columns_item_data in self.columns:
            columns_item = columns_item_data.to_dict()
            columns.append(columns_item)

        contacts_only = self.contacts_only

        table = self.table

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "columns": columns,
                "contacts_only": contacts_only,
                "table": table,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.table_column import TableColumn

        d = dict(src_dict)
        columns = []
        _columns = d.pop("columns")
        for columns_item_data in _columns:
            columns_item = TableColumn.from_dict(columns_item_data)

            columns.append(columns_item)

        contacts_only = d.pop("contacts_only")

        table = d.pop("table")

        table_response = cls(
            columns=columns,
            contacts_only=contacts_only,
            table=table,
        )

        table_response.additional_properties = d
        return table_response

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
