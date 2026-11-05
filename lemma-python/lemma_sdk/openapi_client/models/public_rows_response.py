from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.public_column_item import PublicColumnItem
    from ..models.public_rows_response_rows_item import PublicRowsResponseRowsItem


T = TypeVar("T", bound="PublicRowsResponse")


@_attrs_define
class PublicRowsResponse:
    """
    Attributes:
        columns (list[PublicColumnItem]):
        rows (list[PublicRowsResponseRowsItem]): Every row, at most 500. Dates and times are ISO 8601; a JSON column is
            its own lists and objects.
        table (str):
        truncated (bool): More than 500 rows matched, so these are the first of them.
    """

    columns: list[PublicColumnItem]
    rows: list[PublicRowsResponseRowsItem]
    table: str
    truncated: bool
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

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "columns": columns,
                "rows": rows,
                "table": table,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.public_column_item import PublicColumnItem
        from ..models.public_rows_response_rows_item import PublicRowsResponseRowsItem

        d = dict(src_dict)
        columns = []
        _columns = d.pop("columns")
        for columns_item_data in _columns:
            columns_item = PublicColumnItem.from_dict(columns_item_data)

            columns.append(columns_item)

        rows = []
        _rows = d.pop("rows")
        for rows_item_data in _rows:
            rows_item = PublicRowsResponseRowsItem.from_dict(rows_item_data)

            rows.append(rows_item)

        table = d.pop("table")

        truncated = d.pop("truncated")

        public_rows_response = cls(
            columns=columns,
            rows=rows,
            table=table,
            truncated=truncated,
        )

        public_rows_response.additional_properties = d
        return public_rows_response

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
