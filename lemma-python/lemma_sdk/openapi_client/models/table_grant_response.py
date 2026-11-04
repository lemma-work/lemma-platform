from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="TableGrantResponse")


@_attrs_define
class TableGrantResponse:
    """
    Attributes:
        add_columns (list[str]):
        granted_by (UUID):
        read_columns (list[str]):
        table (str):
    """

    add_columns: list[str]
    granted_by: UUID
    read_columns: list[str]
    table: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        add_columns = self.add_columns

        granted_by = str(self.granted_by)

        read_columns = self.read_columns

        table = self.table

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "add_columns": add_columns,
                "granted_by": granted_by,
                "read_columns": read_columns,
                "table": table,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        add_columns = cast(list[str], d.pop("add_columns"))

        granted_by = UUID(d.pop("granted_by"))

        read_columns = cast(list[str], d.pop("read_columns"))

        table = d.pop("table")

        table_grant_response = cls(
            add_columns=add_columns,
            granted_by=granted_by,
            read_columns=read_columns,
            table=table,
        )

        table_grant_response.additional_properties = d
        return table_grant_response

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
