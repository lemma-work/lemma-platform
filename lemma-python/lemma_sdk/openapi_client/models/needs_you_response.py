from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.needs_you_item_response import NeedsYouItemResponse


T = TypeVar("T", bound="NeedsYouResponse")


@_attrs_define
class NeedsYouResponse:
    """
    Attributes:
        items (list[NeedsYouItemResponse]): Oldest first: what has waited longest is listed first.
        limit (int):
        truncated (bool): More was waiting than `limit`; the oldest are listed.
    """

    items: list[NeedsYouItemResponse]
    limit: int
    truncated: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        limit = self.limit

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
                "limit": limit,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.needs_you_item_response import NeedsYouItemResponse

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = NeedsYouItemResponse.from_dict(items_item_data)

            items.append(items_item)

        limit = d.pop("limit")

        truncated = d.pop("truncated")

        needs_you_response = cls(
            items=items,
            limit=limit,
            truncated=truncated,
        )

        needs_you_response.additional_properties = d
        return needs_you_response

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
