from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InboxTotals")


@_attrs_define
class InboxTotals:
    """
    Attributes:
        channels (int): Channels with something new.
        mentions (int): Unread lines naming the reader.
        waiting (int): Questions waiting on the reader's answer.
        contacts (int | Unset): Contacts waiting on a reply: asking for a person, or assigned to the reader and written
            to last by the contact. Default: 0.
    """

    channels: int
    mentions: int
    waiting: int
    contacts: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channels = self.channels

        mentions = self.mentions

        waiting = self.waiting

        contacts = self.contacts

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channels": channels,
                "mentions": mentions,
                "waiting": waiting,
            }
        )
        if contacts is not UNSET:
            field_dict["contacts"] = contacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channels = d.pop("channels")

        mentions = d.pop("mentions")

        waiting = d.pop("waiting")

        contacts = d.pop("contacts", UNSET)

        inbox_totals = cls(
            channels=channels,
            mentions=mentions,
            waiting=waiting,
            contacts=contacts,
        )

        inbox_totals.additional_properties = d
        return inbox_totals

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
