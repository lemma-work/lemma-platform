from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_response import ChannelResponse
    from ..models.inbox_item import InboxItem
    from ..models.inbox_totals import InboxTotals


T = TypeVar("T", bound="InboxResponse")


@_attrs_define
class InboxResponse:
    """
    Attributes:
        totals (InboxTotals):
        contacts (list[ChannelResponse] | Unset): Contacts waiting on a reply, most recently active first: those asking
            for a person, and those assigned to the reader the contact wrote to last. Only for a reader who may see
            contacts.
        mentions (list[InboxItem] | Unset): Unread lines naming the reader, newest first.
        waiting (list[InboxItem] | Unset): Questions waiting on the reader, newest first.
    """

    totals: InboxTotals
    contacts: list[ChannelResponse] | Unset = UNSET
    mentions: list[InboxItem] | Unset = UNSET
    waiting: list[InboxItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        totals = self.totals.to_dict()

        contacts: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.contacts, Unset):
            contacts = []
            for contacts_item_data in self.contacts:
                contacts_item = contacts_item_data.to_dict()
                contacts.append(contacts_item)

        mentions: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.mentions, Unset):
            mentions = []
            for mentions_item_data in self.mentions:
                mentions_item = mentions_item_data.to_dict()
                mentions.append(mentions_item)

        waiting: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.waiting, Unset):
            waiting = []
            for waiting_item_data in self.waiting:
                waiting_item = waiting_item_data.to_dict()
                waiting.append(waiting_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "totals": totals,
            }
        )
        if contacts is not UNSET:
            field_dict["contacts"] = contacts
        if mentions is not UNSET:
            field_dict["mentions"] = mentions
        if waiting is not UNSET:
            field_dict["waiting"] = waiting

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.channel_response import ChannelResponse
        from ..models.inbox_item import InboxItem
        from ..models.inbox_totals import InboxTotals

        d = dict(src_dict)
        totals = InboxTotals.from_dict(d.pop("totals"))

        _contacts = d.pop("contacts", UNSET)
        contacts: list[ChannelResponse] | Unset = UNSET
        if _contacts is not UNSET:
            contacts = []
            for contacts_item_data in _contacts:
                contacts_item = ChannelResponse.from_dict(contacts_item_data)

                contacts.append(contacts_item)

        _mentions = d.pop("mentions", UNSET)
        mentions: list[InboxItem] | Unset = UNSET
        if _mentions is not UNSET:
            mentions = []
            for mentions_item_data in _mentions:
                mentions_item = InboxItem.from_dict(mentions_item_data)

                mentions.append(mentions_item)

        _waiting = d.pop("waiting", UNSET)
        waiting: list[InboxItem] | Unset = UNSET
        if _waiting is not UNSET:
            waiting = []
            for waiting_item_data in _waiting:
                waiting_item = InboxItem.from_dict(waiting_item_data)

                waiting.append(waiting_item)

        inbox_response = cls(
            totals=totals,
            contacts=contacts,
            mentions=mentions,
            waiting=waiting,
        )

        inbox_response.additional_properties = d
        return inbox_response

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
