from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.contact_row_values import ContactRowValues


T = TypeVar("T", bound="ContactRow")


@_attrs_define
class ContactRow:
    """One row the pod keeps about a contact, for a request to see their data.

    Attributes:
        table (str):
        values (ContactRowValues):
    """

    table: str
    values: ContactRowValues
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        table = self.table

        values = self.values.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "table": table,
                "values": values,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.contact_row_values import ContactRowValues

        d = dict(src_dict)
        table = d.pop("table")

        values = ContactRowValues.from_dict(d.pop("values"))

        contact_row = cls(
            table=table,
            values=values,
        )

        contact_row.additional_properties = d
        return contact_row

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
