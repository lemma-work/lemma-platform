from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.operation_detail import OperationDetail


T = TypeVar("T", bound="OperationDetailsBatchResponse")


@_attrs_define
class OperationDetailsBatchResponse:
    """Batch response containing full metadata for multiple operations.

    Attributes:
        connector_id (str): Connector identifier.
        items (list[OperationDetail]): Operation details for the requested operations.
        returned_count (int): Number of operation details returned in this response.
        total_operations (int | Unset): Operations the connector exposes in total. Greater than `returned_count` means
            the unnamed request was capped. Default: 0.
    """

    connector_id: str
    items: list[OperationDetail]
    returned_count: int
    total_operations: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connector_id = self.connector_id

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        returned_count = self.returned_count

        total_operations = self.total_operations

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector_id": connector_id,
                "items": items,
                "returned_count": returned_count,
            }
        )
        if total_operations is not UNSET:
            field_dict["total_operations"] = total_operations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.operation_detail import OperationDetail

        d = dict(src_dict)
        connector_id = d.pop("connector_id")

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = OperationDetail.from_dict(items_item_data)

            items.append(items_item)

        returned_count = d.pop("returned_count")

        total_operations = d.pop("total_operations", UNSET)

        operation_details_batch_response = cls(
            connector_id=connector_id,
            items=items,
            returned_count=returned_count,
            total_operations=total_operations,
        )

        operation_details_batch_response.additional_properties = d
        return operation_details_batch_response

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
