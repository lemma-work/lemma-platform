from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.bulk_create_records_request_records_item import (
        BulkCreateRecordsRequestRecordsItem,
    )


T = TypeVar("T", bound="BulkCreateRecordsRequest")


@_attrs_define
class BulkCreateRecordsRequest:
    """Schema for bulk creating records.

    Attributes:
        records (list[BulkCreateRecordsRequestRecordsItem]): List of record payload objects to insert. At most 1000 per
            request.
        upsert (bool | Unset): When true, insert records and update existing rows that conflict on the table primary
            key. Default: False.
    """

    records: list[BulkCreateRecordsRequestRecordsItem]
    upsert: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        records = []
        for records_item_data in self.records:
            records_item = records_item_data.to_dict()
            records.append(records_item)

        upsert = self.upsert

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "records": records,
            }
        )
        if upsert is not UNSET:
            field_dict["upsert"] = upsert

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bulk_create_records_request_records_item import (
            BulkCreateRecordsRequestRecordsItem,
        )

        d = dict(src_dict)
        records = []
        _records = d.pop("records")
        for records_item_data in _records:
            records_item = BulkCreateRecordsRequestRecordsItem.from_dict(
                records_item_data
            )

            records.append(records_item)

        upsert = d.pop("upsert", UNSET)

        bulk_create_records_request = cls(
            records=records,
            upsert=upsert,
        )

        bulk_create_records_request.additional_properties = d
        return bulk_create_records_request

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
