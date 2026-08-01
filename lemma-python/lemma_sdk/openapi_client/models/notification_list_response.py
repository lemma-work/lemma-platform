from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.notification_response import NotificationResponse


T = TypeVar("T", bound="NotificationListResponse")


@_attrs_define
class NotificationListResponse:
    """
    Attributes:
        items (list[NotificationResponse]):
        unread_count (int | Unset):  Default: 0.
    """

    items: list[NotificationResponse]
    unread_count: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        unread_count = self.unread_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
            }
        )
        if unread_count is not UNSET:
            field_dict["unread_count"] = unread_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.notification_response import NotificationResponse

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = NotificationResponse.from_dict(items_item_data)

            items.append(items_item)

        unread_count = d.pop("unread_count", UNSET)

        notification_list_response = cls(
            items=items,
            unread_count=unread_count,
        )

        notification_list_response.additional_properties = d
        return notification_list_response

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
