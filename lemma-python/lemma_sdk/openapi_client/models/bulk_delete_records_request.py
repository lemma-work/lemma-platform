from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BulkDeleteRecordsRequest")


@_attrs_define
class BulkDeleteRecordsRequest:
    """Schema for bulk deleting records.

    Attributes:
        record_ids (list[int | str | UUID]): Primary key values to delete.
    """

    record_ids: list[int | str | UUID]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        record_ids = []
        for record_ids_item_data in self.record_ids:
            record_ids_item: int | str
            if isinstance(record_ids_item_data, UUID):
                record_ids_item = str(record_ids_item_data)
            else:
                record_ids_item = record_ids_item_data
            record_ids.append(record_ids_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "record_ids": record_ids,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        record_ids = []
        _record_ids = d.pop("record_ids")
        for record_ids_item_data in _record_ids:

            def _parse_record_ids_item(data: object) -> int | str | UUID:
                try:
                    if not isinstance(data, str):
                        raise TypeError()
                    record_ids_item_type_2 = UUID(data)

                    return record_ids_item_type_2
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                return cast(int | str | UUID, data)

            record_ids_item = _parse_record_ids_item(record_ids_item_data)

            record_ids.append(record_ids_item)

        bulk_delete_records_request = cls(
            record_ids=record_ids,
        )

        bulk_delete_records_request.additional_properties = d
        return bulk_delete_records_request

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
