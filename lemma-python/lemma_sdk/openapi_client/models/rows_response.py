from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.read_column_item import ReadColumnItem
    from ..models.rows_response_rows_item import RowsResponseRowsItem


T = TypeVar("T", bound="RowsResponse")


@_attrs_define
class RowsResponse:
    """
    Attributes:
        columns (list[ReadColumnItem]): The open columns, in order.
        rows (list[RowsResponseRowsItem]): Every row's open columns, at most 500, in the order the pod chose. Dates and
            times are ISO 8601.
        table (str):
    """

    columns: list[ReadColumnItem]
    rows: list[RowsResponseRowsItem]
    table: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        columns = []
        for columns_item_data in self.columns:
            columns_item = columns_item_data.to_dict()
            columns.append(columns_item)

        rows = []
        for rows_item_data in self.rows:
            rows_item = rows_item_data.to_dict()
            rows.append(rows_item)

        table = self.table

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "columns": columns,
                "rows": rows,
                "table": table,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.read_column_item import ReadColumnItem
        from ..models.rows_response_rows_item import RowsResponseRowsItem

        d = dict(src_dict)
        columns = []
        _columns = d.pop("columns")
        for columns_item_data in _columns:
            columns_item = ReadColumnItem.from_dict(columns_item_data)

            columns.append(columns_item)

        rows = []
        _rows = d.pop("rows")
        for rows_item_data in _rows:
            rows_item = RowsResponseRowsItem.from_dict(rows_item_data)

            rows.append(rows_item)

        table = d.pop("table")

        rows_response = cls(
            columns=columns,
            rows=rows,
            table=table,
        )

        rows_response.additional_properties = d
        return rows_response

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
