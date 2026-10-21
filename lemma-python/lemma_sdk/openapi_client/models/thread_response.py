from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_message_response import ChannelMessageResponse


T = TypeVar("T", bound="ThreadResponse")


@_attrs_define
class ThreadResponse:
    """
    Attributes:
        has_more (bool): Whether there are more replies in the direction read: newer ones, or with `before_seq` or
            `newest`, older ones.
        items (list[ChannelMessageResponse]): Replies, oldest first.
        root (ChannelMessageResponse):
        next_after_seq (int | None | Unset): Pass as `after_seq` for the newer page.
        next_before_seq (int | None | Unset): Reading backwards: pass as `before_seq` for the older page.
    """

    has_more: bool
    items: list[ChannelMessageResponse]
    root: ChannelMessageResponse
    next_after_seq: int | None | Unset = UNSET
    next_before_seq: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        has_more = self.has_more

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        root = self.root.to_dict()

        next_after_seq: int | None | Unset
        if isinstance(self.next_after_seq, Unset):
            next_after_seq = UNSET
        else:
            next_after_seq = self.next_after_seq

        next_before_seq: int | None | Unset
        if isinstance(self.next_before_seq, Unset):
            next_before_seq = UNSET
        else:
            next_before_seq = self.next_before_seq

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "has_more": has_more,
                "items": items,
                "root": root,
            }
        )
        if next_after_seq is not UNSET:
            field_dict["next_after_seq"] = next_after_seq
        if next_before_seq is not UNSET:
            field_dict["next_before_seq"] = next_before_seq

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.channel_message_response import ChannelMessageResponse

        d = dict(src_dict)
        has_more = d.pop("has_more")

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = ChannelMessageResponse.from_dict(items_item_data)

            items.append(items_item)

        root = ChannelMessageResponse.from_dict(d.pop("root"))

        def _parse_next_after_seq(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        next_after_seq = _parse_next_after_seq(d.pop("next_after_seq", UNSET))

        def _parse_next_before_seq(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        next_before_seq = _parse_next_before_seq(d.pop("next_before_seq", UNSET))

        thread_response = cls(
            has_more=has_more,
            items=items,
            root=root,
            next_after_seq=next_after_seq,
            next_before_seq=next_before_seq,
        )

        thread_response.additional_properties = d
        return thread_response

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
