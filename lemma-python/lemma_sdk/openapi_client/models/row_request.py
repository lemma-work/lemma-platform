from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.row_request_values import RowRequestValues


T = TypeVar("T", bound="RowRequest")


@_attrs_define
class RowRequest:
    """
    Attributes:
        table (str):
        values (RowRequestValues | Unset):
    """

    table: str
    values: RowRequestValues | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        table = self.table

        values: dict[str, Any] | Unset = UNSET
        if not isinstance(self.values, Unset):
            values = self.values.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "table": table,
            }
        )
        if values is not UNSET:
            field_dict["values"] = values

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.row_request_values import RowRequestValues

        d = dict(src_dict)
        table = d.pop("table")

        _values = d.pop("values", UNSET)
        values: RowRequestValues | Unset
        if isinstance(_values, Unset):
            values = UNSET
        else:
            values = RowRequestValues.from_dict(_values)

        row_request = cls(
            table=table,
            values=values,
        )

        row_request.additional_properties = d
        return row_request

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
