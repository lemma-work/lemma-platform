from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.write_intent import WriteIntent
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.bulk_update_records_request_records_item import (
        BulkUpdateRecordsRequestRecordsItem,
    )


T = TypeVar("T", bound="BulkUpdateRecordsRequest")


@_attrs_define
class BulkUpdateRecordsRequest:
    """Schema for bulk updating records.

    Attributes:
        records (list[BulkUpdateRecordsRequestRecordsItem]): List of record updates. Each item must include the table
            primary key field. At most 1000 per request.
        intent (WriteIntent | Unset):
    """

    records: list[BulkUpdateRecordsRequestRecordsItem]
    intent: WriteIntent | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        records = []
        for records_item_data in self.records:
            records_item = records_item_data.to_dict()
            records.append(records_item)

        intent: str | Unset = UNSET
        if not isinstance(self.intent, Unset):
            intent = self.intent.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "records": records,
            }
        )
        if intent is not UNSET:
            field_dict["intent"] = intent

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bulk_update_records_request_records_item import (
            BulkUpdateRecordsRequestRecordsItem,
        )

        d = dict(src_dict)
        records = []
        _records = d.pop("records")
        for records_item_data in _records:
            records_item = BulkUpdateRecordsRequestRecordsItem.from_dict(
                records_item_data
            )

            records.append(records_item)

        _intent = d.pop("intent", UNSET)
        intent: WriteIntent | Unset
        if isinstance(_intent, Unset):
            intent = UNSET
        else:
            intent = WriteIntent(_intent)

        bulk_update_records_request = cls(
            records=records,
            intent=intent,
        )

        bulk_update_records_request.additional_properties = d
        return bulk_update_records_request

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
