from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.slack_channel_person_response import SlackChannelPersonResponse


T = TypeVar("T", bound="SlackChannelPeopleResponse")


@_attrs_define
class SlackChannelPeopleResponse:
    """
    Attributes:
        items (list[SlackChannelPersonResponse]):
        more (int | Unset):  Default: 0.
    """

    items: list[SlackChannelPersonResponse]
    more: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        more = self.more

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
            }
        )
        if more is not UNSET:
            field_dict["more"] = more

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.slack_channel_person_response import SlackChannelPersonResponse

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = SlackChannelPersonResponse.from_dict(items_item_data)

            items.append(items_item)

        more = d.pop("more", UNSET)

        slack_channel_people_response = cls(
            items=items,
            more=more,
        )

        slack_channel_people_response.additional_properties = d
        return slack_channel_people_response

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
