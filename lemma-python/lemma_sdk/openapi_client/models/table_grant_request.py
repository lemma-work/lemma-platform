from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TableGrantRequest")


@_attrs_define
class TableGrantRequest:
    """
    Attributes:
        add_columns (list[str] | Unset): Columns they may fill in a new row; empty to only read.
        read_columns (list[str] | Unset): Columns everybody in the group may read.
    """

    add_columns: list[str] | Unset = UNSET
    read_columns: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        add_columns: list[str] | Unset = UNSET
        if not isinstance(self.add_columns, Unset):
            add_columns = self.add_columns

        read_columns: list[str] | Unset = UNSET
        if not isinstance(self.read_columns, Unset):
            read_columns = self.read_columns

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if add_columns is not UNSET:
            field_dict["add_columns"] = add_columns
        if read_columns is not UNSET:
            field_dict["read_columns"] = read_columns

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        add_columns = cast(list[str], d.pop("add_columns", UNSET))

        read_columns = cast(list[str], d.pop("read_columns", UNSET))

        table_grant_request = cls(
            add_columns=add_columns,
            read_columns=read_columns,
        )

        table_grant_request.additional_properties = d
        return table_grant_request

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
