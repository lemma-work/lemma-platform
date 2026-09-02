from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.datastore_query_response_items_item import (
        DatastoreQueryResponseItemsItem,
    )


T = TypeVar("T", bound="DatastoreQueryResponse")


@_attrs_define
class DatastoreQueryResponse:
    """Schema for read-only datastore query results.

    Attributes:
        items (list[DatastoreQueryResponseItemsItem]):
        total (int): Number of rows in `items`. This is what came back, not how many rows the query matched: when
            `truncated` is true the result was cut at the deployment's row cap and more rows exist.
        truncated (bool | Unset): True when the row cap cut the result short, so `items` is a prefix of the query's real
            answer. Narrow the query (add a WHERE, aggregate, or LIMIT) to see the rest. Reported because a capped result is
            otherwise indistinguishable from a complete one. Default: False.
    """

    items: list[DatastoreQueryResponseItemsItem]
    total: int
    truncated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        total = self.total

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
                "total": total,
            }
        )
        if truncated is not UNSET:
            field_dict["truncated"] = truncated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.datastore_query_response_items_item import (
            DatastoreQueryResponseItemsItem,
        )

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = DatastoreQueryResponseItemsItem.from_dict(items_item_data)

            items.append(items_item)

        total = d.pop("total")

        truncated = d.pop("truncated", UNSET)

        datastore_query_response = cls(
            items=items,
            total=total,
            truncated=truncated,
        )

        datastore_query_response.additional_properties = d
        return datastore_query_response

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
